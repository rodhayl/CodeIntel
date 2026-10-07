import bisect
import tree_sitter

import tree_sitter_python
import tree_sitter_javascript
import tree_sitter_typescript

from pathlib import Path
from typing import List, Tuple, Optional, Dict
from codeintel.core.models import (
    Entity, Relation, Chunk, EntityKind, RelationType, TrustClass
)
from codeintel.core.identity import (
    make_entity_id, make_relation_id, make_chunk_id, canonical_hash
)

from codeintel.parsing.chunk_partition import partition_entity_chunks
from codeintel.parsing.python_accessors import normalize_python_property_accessors


def strip_code_suffix(rel_path: str) -> str:
    """Safely removes language extension without string rstrip character-set bugs."""
    p = Path(rel_path)
    suffixes = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
    if p.suffix.lower() in suffixes:
        stem = p.as_posix()[: -len(p.suffix)]
    else:
        stem = p.as_posix()
    namespace = stem.replace("/", ".").strip(".")
    # Valid files such as '..py' have only dots left after suffix removal.
    # Preserve their full relative spelling as the fallback instead of
    # manufacturing an empty identity or merging distinct dotted filenames.
    return namespace or p.as_posix().replace("/", ".")

def clean_docstring(raw_text: str) -> Optional[str]:
    if not raw_text:
        return None
    s = raw_text.strip()
    if s.startswith('"""') and s.endswith('"""'):
        s = s[3:-3]
    elif s.startswith("'''") and s.endswith("'''"):
        s = s[3:-3]
    elif s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    elif s.startswith("'") and s.endswith("'"):
        s = s[1:-1]
    s = s.strip()
    return s if s else None


class TreeSitterCodeParser:
    """Canonical Python/JS/TS syntax extraction and literal source partitioning."""

    def __init__(self):
        self._py_lang = tree_sitter.Language(tree_sitter_python.language())
        self._ts_lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())
        self._tsx_lang = tree_sitter.Language(tree_sitter_typescript.language_tsx())
        self._js_lang = tree_sitter.Language(tree_sitter_javascript.language())

    def parse_file(
        self,
        repo_id: str,
        file_id: str,
        generation_id: str,
        rel_path: str,
        content: str,
    ) -> Tuple[List[Entity], List[Relation], List[Chunk]]:
        """Extract syntax once, then assign every meaningful byte one owner."""
        lower = rel_path.lower()
        if lower.endswith(".py"):
            parser = tree_sitter.Parser(self._py_lang)
            entities, relations = self._parse_python(
                repo_id, file_id, generation_id, rel_path, content, parser
            )
            entities, relations = normalize_python_property_accessors(
                entities=entities, relations=relations, content=content
            )
        elif lower.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
            language = self._tsx_lang if lower.endswith(".tsx") else (
                self._ts_lang if lower.endswith(".ts") else self._js_lang
            )
            entities, relations = self._parse_typescript(
                repo_id, file_id, generation_id, rel_path, content,
                tree_sitter.Parser(language),
            )
        else:
            return self._fallback_line_chunks(file_id, generation_id, rel_path, content)
        chunks = partition_entity_chunks(
            entities=entities, content=content,
            rel_path=rel_path, file_id=file_id, generation_id=generation_id,
        )
        return entities, relations, chunks

    def _fallback_line_chunks(
        self, file_id: str, generation_id: str, rel_path: str, content: str,
    ) -> Tuple[List[Entity], List[Relation], List[Chunk]]:
        """Represent unsupported UTF-8 text with an exact byte-coordinate span."""
        raw = content.encode("utf-8", errors="strict")
        span = (1, 0, raw.count(b"\n") + 1, len(raw.rsplit(b"\n", 1)[-1]))
        content_hash = canonical_hash(content)
        return [], [], [Chunk(
            chunk_id=make_chunk_id(file_id, generation_id, span, content_hash),
            file_id=file_id, generation_id=generation_id, rel_path=rel_path,
            span=span, content=content, content_hash=content_hash, entity_ids=[],
        )]

    def _parse_python(
        self,
        repo_id: str,
        file_id: str,
        generation_id: str,
        rel_path: str,
        content: str,
        parser: Optional[tree_sitter.Parser] = None
    ) -> Tuple[List[Entity], List[Relation]]:
        # Source rows are LF-delimited; Unicode separators inside a literal are
        # source characters, not extra Tree-sitter rows.
        parts = content.split("\n")
        mod_name = strip_code_suffix(rel_path)
        mod_span = (1, 0, len(parts), len(parts[-1].encode("utf-8")))
        mod_id = make_entity_id(file_id, mod_name, EntityKind.MODULE.value)

        content_bytes = content.encode("utf-8")
        p = parser or tree_sitter.Parser(self._py_lang)
        tree = p.parse(content_bytes)

        def get_span(node: tree_sitter.Node) -> Tuple[int, int, int, int]:
            sp = node.start_point
            ep = node.end_point
            return (int(sp[0]) + 1, int(sp[1]), int(ep[0]) + 1, int(ep[1]))

        def get_node_text(node: Optional[tree_sitter.Node]) -> str:
            if node is None:
                return ""
            return content_bytes[int(node.start_byte):int(node.end_byte)].decode("utf-8", errors="replace")

        def extract_docstring(body_node: Optional[tree_sitter.Node]) -> Optional[str]:
            if not body_node:
                return None
            for child in body_node.named_children:
                if child.type == "expression_statement":
                    first_sub = child.named_child(0) if child.named_child_count > 0 else None
                    if first_sub and first_sub.type in ("string", "concatenated_string"):
                        return clean_docstring(get_node_text(first_sub))
                break
            return None

        # Module docstring
        mod_docstring = None
        for child in tree.root_node.named_children:
            if child.type == "expression_statement":
                first_sub = child.named_child(0) if child.named_child_count > 0 else None
                if first_sub and first_sub.type in ("string", "concatenated_string"):
                    mod_docstring = clean_docstring(get_node_text(first_sub))
            break

        entities: List[Entity] = []
        relations: List[Relation] = []
        definition_counts: Dict[str, int] = {}
        first_definition_parents: Dict[str, str] = {}

        def definition_id(qname: str, kind: EntityKind, parent_id: str) -> str:
            base_id = make_entity_id(file_id, qname, kind.value)
            # Same-class method duplicates may be property accessor variants;
            # their dedicated normalizer assigns semantic getter/setter IDs.
            if kind == EntityKind.METHOD and first_definition_parents.get(base_id) == parent_id:
                return base_id
            first_definition_parents.setdefault(base_id, parent_id)
            ordinal = definition_counts.get(base_id, 0) + 1
            definition_counts[base_id] = ordinal
            if ordinal == 1:
                return base_id
            # Repeated declarations have distinct source identities even when
            # their lexical qualified names coincide.
            return make_entity_id(file_id, f"{qname}#python-definition-{ordinal}", kind.value)

        mod_entity = Entity(
            entity_id=mod_id,
            repo_id=repo_id,
            file_id=file_id,
            generation_id=generation_id,
            name=mod_name.split(".")[-1],
            qualified_name=mod_name,
            kind=EntityKind.MODULE,
            span=mod_span,
            docstring=mod_docstring,
            signature=f"module {mod_name}"
        )
        entities.append(mod_entity)

        # Iterative stack: (node, scope, parent_id, in_class, outer_node_for_span)
        stack: List[Tuple[tree_sitter.Node, str, str, bool, Optional[tree_sitter.Node]]] = []
        for child in reversed(tree.root_node.named_children):
            stack.append((child, mod_name, mod_id, False, None))

        while stack:
            node, scope, parent_id, in_class, outer_node = stack.pop()

            if node.type == "decorated_definition":
                # Find inner definition (function_definition, async_function_definition, class_definition)
                inner_def = None
                for c in node.named_children:
                    if c.type in ("function_definition", "async_function_definition", "class_definition"):
                        inner_def = c
                        break
                if inner_def:
                    stack.append((inner_def, scope, parent_id, in_class, node))
                continue

            if node.type in ("function_definition", "async_function_definition"):
                name_node = node.child_by_field_name("name")
                name = get_node_text(name_node) if name_node else "anonymous"
                qname = f"{scope}.{name}" if scope else name
                span_node = outer_node if outer_node else node
                span = get_span(span_node)
                kind = EntityKind.METHOD if in_class else EntityKind.FUNCTION
                ent_id = definition_id(qname, kind, parent_id)

                # Signature
                body_node = node.child_by_field_name("body")
                if body_node:
                    sig_bytes = content_bytes[int(span_node.start_byte):int(body_node.start_byte)].strip()
                    sig_text = sig_bytes.decode("utf-8", errors="replace").rstrip(":").strip()
                else:
                    sig_text = f"def {name}()"

                doc = extract_docstring(body_node)

                entities.append(Entity(
                    entity_id=ent_id,
                    repo_id=repo_id,
                    file_id=file_id,
                    generation_id=generation_id,
                    name=name,
                    qualified_name=qname,
                    kind=kind,
                    span=span,
                    docstring=doc,
                    signature=sig_text,
                    properties={"parent_id": parent_id, "rel_path": rel_path}
                ))

                rel_id = make_relation_id(parent_id, RelationType.DEFINES.value, ent_id, file_id, span)
                relations.append(Relation(
                    relation_id=rel_id,
                    repo_id=repo_id,
                    generation_id=generation_id,
                    source_id=parent_id,
                    target_id=ent_id,
                    rel_type=RelationType.DEFINES,
                    file_id=file_id,
                    span=span,
                    trust_class=TrustClass.EXACT
                ))

                # Extract CALLS and walk inner definitions
                if body_node:
                    b_stack = [body_node]
                    while b_stack:
                        b_curr = b_stack.pop()
                        for b_child in b_curr.named_children:
                            if b_child.type in ("function_definition", "async_function_definition", "class_definition", "decorated_definition"):
                                stack.append((b_child, qname, ent_id, False, None))
                                continue
                            if b_child.type == "lambda":
                                # Deferred lambda bodies have no named entity here;
                                # defaults are evaluated eagerly by the outer owner.
                                parameters = b_child.child_by_field_name("parameters")
                                if parameters is not None:
                                    b_stack.append(parameters)
                                continue
                            if b_child.type == "call":
                                func_node = b_child.child_by_field_name("function")
                                if func_node:
                                    if func_node.type == "attribute":
                                        attr_node = func_node.child_by_field_name("attribute")
                                        obj_node = func_node.child_by_field_name("object")
                                        call_name = get_node_text(attr_node) if attr_node else get_node_text(func_node)
                                        receiver_name = get_node_text(obj_node).strip() if obj_node else None
                                        is_this = receiver_name in ("self", "cls")
                                    else:
                                        call_name = get_node_text(func_node)
                                        receiver_name = None
                                        is_this = False
                                    if call_name:
                                        c_span = get_span(b_child)
                                        t_id = make_entity_id(file_id, call_name, EntityKind.FUNCTION.value)
                                        r_id = make_relation_id(ent_id, RelationType.CALLS.value, t_id, file_id, c_span)
                                        relations.append(Relation(
                                            relation_id=r_id,
                                            repo_id=repo_id,
                                            generation_id=generation_id,
                                            source_id=ent_id,
                                            target_id=t_id,
                                            rel_type=RelationType.CALLS,
                                            file_id=file_id,
                                            span=c_span,
                                            trust_class=TrustClass.EXACT,
                                            properties={
                                                "callee_name": call_name,
                                                "receiver_name": receiver_name,
                                                "is_this": is_this,
                                                "enclosing_class_id": parent_id if in_class else None
                                            }
                                        ))
                            b_stack.append(b_child)
                continue

            elif node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                name = get_node_text(name_node) if name_node else "anonymous"
                qname = f"{scope}.{name}" if scope else name
                span_node = outer_node if outer_node else node
                span = get_span(span_node)
                ent_id = definition_id(qname, EntityKind.CLASS, parent_id)

                body_node = node.child_by_field_name("body")
                if body_node:
                    sig_bytes = content_bytes[int(span_node.start_byte):int(body_node.start_byte)].strip()
                    sig_text = sig_bytes.decode("utf-8", errors="replace").rstrip(":").strip()
                else:
                    sig_text = f"class {name}"

                doc = extract_docstring(body_node)

                entities.append(Entity(
                    entity_id=ent_id,
                    repo_id=repo_id,
                    file_id=file_id,
                    generation_id=generation_id,
                    name=name,
                    qualified_name=qname,
                    kind=EntityKind.CLASS,
                    span=span,
                    docstring=doc,
                    signature=sig_text,
                    properties={"parent_id": parent_id, "rel_path": rel_path}
                ))

                rel_id = make_relation_id(parent_id, RelationType.DEFINES.value, ent_id, file_id, span)
                relations.append(Relation(
                    relation_id=rel_id,
                    repo_id=repo_id,
                    generation_id=generation_id,
                    source_id=parent_id,
                    target_id=ent_id,
                    rel_type=RelationType.DEFINES,
                    file_id=file_id,
                    span=span,
                    trust_class=TrustClass.EXACT
                ))

                if body_node:
                    for child in reversed(body_node.named_children):
                        stack.append((child, qname, ent_id, True, None))
                continue

            # Fallthrough for other container statements (if, while, try, with, etc.)
            for child in reversed(node.named_children):
                stack.append((child, scope, parent_id, in_class, None))

        return entities, relations

    def _parse_typescript(
        self,
        repo_id: str,
        file_id: str,
        generation_id: str,
        rel_path: str,
        content: str,
        parser: tree_sitter.Parser
    ) -> Tuple[List[Entity], List[Relation]]:
        raw_lines = content.split("\n")
        content_bytes = content.encode("utf-8")
        tree = parser.parse(content_bytes)

        entities: List[Entity] = []
        relations: List[Relation] = []

        line_offsets = [0] + [
            offset + 1 for offset, value in enumerate(content_bytes) if value == 0x0A
        ]

        definition_counts: Dict[str, int] = {}

        def definition_id(qname: str, kind: EntityKind, accessor_role: Optional[str] = None) -> str:
            # Overload signatures, implementations and repeated declarations are
            # distinct source declarations, not compiler-resolved overloads.
            # A setter has its own semantic identity even if it precedes a getter.
            identity_name = (
                f"{qname}#syntax-property-setter" if accessor_role == "setter" else qname
            )
            base_id = make_entity_id(file_id, identity_name, kind.value)
            ordinal = definition_counts.get(base_id, 0) + 1
            definition_counts[base_id] = ordinal
            if ordinal == 1:
                return base_id
            return make_entity_id(
                file_id, f"{identity_name}#syntax-definition-{ordinal}", kind.value
            )

        def get_span(node: tree_sitter.Node) -> Tuple[int, int, int, int]:
            try:
                sb = max(0, min(len(content_bytes), int(node.start_byte)))
                eb = max(sb, min(len(content_bytes), int(node.end_byte)))
                sl = bisect.bisect_right(line_offsets, sb)
                sc = sb - line_offsets[sl - 1]
                el = bisect.bisect_right(line_offsets, eb)
                ec = eb - line_offsets[el - 1]
                return (sl, sc, el, ec)
            except Exception:
                return (1, 0, max(1, len(raw_lines)), 0)

        def get_node_text(node: Optional[tree_sitter.Node]) -> str:
            if node is None:
                return ""
            try:
                sb = max(0, min(len(content_bytes), int(node.start_byte)))
                eb = max(sb, min(len(content_bytes), int(node.end_byte)))
                return content_bytes[sb:eb].decode("utf-8", errors="replace")
            except Exception:
                return ""

        mod_name = strip_code_suffix(rel_path)
        mod_span = (1, 0, len(raw_lines), len(raw_lines[-1].encode("utf-8")))
        mod_id = make_entity_id(file_id, mod_name, EntityKind.MODULE.value)
        entities.append(Entity(
            entity_id=mod_id,
            repo_id=repo_id,
            file_id=file_id,
            generation_id=generation_id,
            name=mod_name.split(".")[-1],
            qualified_name=mod_name,
            kind=EntityKind.MODULE,
            span=mod_span,
            docstring=None,
            signature=f"module {mod_name}"
        ))

        keywords = {"if", "for", "while", "catch", "switch", "import", "export", "function", "return", "typeof", "void", "new", "delete", "async", "await"}

        def extract_calls(parent_ent_id: str, body_node: Optional[tree_sitter.Node], enclosing_cls_id: Optional[str] = None, enclosing_cls_name: Optional[str] = None, type_map: Optional[Dict[str, str]] = None) -> None:
            if not body_node:
                return
            t_map = dict(type_map or {})
            nested_scopes = {
                "function_declaration", "function_expression", "arrow_function",
                "generator_function_declaration", "generator_function",
                "class_declaration", "class", "method_definition", "interface_declaration",
            }

            def discard_binding(binding: Optional[tree_sitter.Node]) -> None:
                pending = [binding] if binding is not None else []
                while pending:
                    part = pending.pop()
                    if part.type in ("identifier", "type_identifier", "shorthand_property_identifier_pattern"):
                        t_map.pop(get_node_text(part), None)
                    elif part.type in ("object_pattern", "array_pattern", "pair_pattern",
                                       "assignment_pattern", "object_assignment_pattern", "rest_pattern"):
                        pending.extend(part.named_children)

            # This extractor is not a lexical/dataflow binding engine. A local
            # declaration or reassignment can invalidate a parameter's type.
            # Conservatively drop that evidence throughout this callable rather
            # than promote a shadowed receiver to a false RESOLVED graph edge.
            binding_stack = [body_node]
            while binding_stack:
                current = binding_stack.pop()
                if current is not body_node and current.type in nested_scopes:
                    # Declarations bind their names in the enclosing scope,
                    # although their bodies must not inherit this call owner.
                    if current.type in ("function_declaration", "generator_function_declaration",
                                        "class_declaration"):
                        discard_binding(current.child_by_field_name("name"))
                    continue
                field = {
                    "variable_declarator": "name", "catch_clause": "parameter",
                    "for_in_statement": "left", "assignment_expression": "left",
                    "augmented_assignment_expression": "left", "update_expression": "argument",
                }.get(current.type)
                if field is not None:
                    discard_binding(current.child_by_field_name(field))
                binding_stack.extend(current.named_children)

            b_stack = [body_node]
            while b_stack:
                curr = b_stack.pop()
                # Include expression-bodied arrows' root call, but never enter
                # a returned/nested callable, including when it is the body root.
                if curr.type in nested_scopes:
                    continue
                if curr.type in ("call_expression", "new_expression"):
                    fn_node = curr.child_by_field_name("function") or curr.child_by_field_name("constructor") or (curr.named_child(0) if curr.named_child_count > 0 else None)
                    if fn_node:
                        call_name = None
                        receiver_name = None
                        receiver_type = None
                        is_this = False
                        is_new = (curr.type == "new_expression")
                        if fn_node.type in ("identifier", "property_identifier", "type_identifier"):
                            call_name = get_node_text(fn_node)
                        elif fn_node.type == "member_expression":
                            prop_node = fn_node.child_by_field_name("property")
                            obj_node = fn_node.child_by_field_name("object")
                            if prop_node:
                                call_name = get_node_text(prop_node)
                            else:
                                call_name = get_node_text(fn_node).split(".")[-1]
                            if obj_node:
                                receiver_name = get_node_text(obj_node).strip()
                                is_this = (receiver_name == "this")
                                receiver_type = t_map.get(receiver_name)
                        else:
                            raw_fn = get_node_text(fn_node)
                            if "(" not in raw_fn and raw_fn:
                                call_name = raw_fn.split(".")[-1].strip()
                                if "." in raw_fn:
                                    receiver_name = raw_fn.split(".")[0].strip()
                                    is_this = (receiver_name == "this")
                                    receiver_type = t_map.get(receiver_name)

                        if call_name and call_name not in keywords and len(call_name) < 128:
                            c_span = get_span(curr)
                            t_id = make_entity_id(file_id, call_name, EntityKind.FUNCTION.value)
                            r_id = make_relation_id(parent_ent_id, RelationType.CALLS.value, t_id, file_id, c_span)
                            relations.append(Relation(
                                relation_id=r_id,
                                repo_id=repo_id,
                                generation_id=generation_id,
                                source_id=parent_ent_id,
                                target_id=t_id,
                                rel_type=RelationType.CALLS,
                                file_id=file_id,
                                span=c_span,
                                trust_class=TrustClass.EXACT,
                                properties={
                                    "callee_name": call_name,
                                    "receiver_name": receiver_name,
                                    "receiver_type": receiver_type,
                                    "is_this": is_this,
                                    "is_new": is_new,
                                    "enclosing_class_id": enclosing_cls_id,
                                    "enclosing_class_name": enclosing_cls_name
                                }
                            ))
                b_stack.extend(reversed(curr.named_children))

        stack: List[Tuple[tree_sitter.Node, str, str, bool]] = []
        for child in reversed(tree.root_node.named_children):
            stack.append((child, mod_name, mod_id, False))

        while stack:
            node, scope, parent_id, in_class = stack.pop()
            t = node.type

            if t in ("export_statement", "export_default_declaration", "program", "statement_block"):
                for child in reversed(node.named_children):
                    stack.append((child, scope, parent_id, in_class))
                continue

            if t in ("function_declaration", "function_signature", "method_definition"):
                name = None
                body_node = None
                params_node = None
                for ch in node.named_children:
                    if ch.type in ("identifier", "property_identifier", "type_identifier") and name is None:
                        name = get_node_text(ch)
                    elif ch.type in ("statement_block", "class_body"):
                        body_node = ch
                    elif ch.type in ("formal_parameters", "parameters"):
                        params_node = ch

                # Extract local parameter types
                local_types: Dict[str, str] = {}
                if params_node:
                    for p_child in params_node.named_children:
                        p_name = None
                        p_type = None
                        for p_sub in p_child.named_children:
                            if p_sub.type in ("identifier", "property_identifier") and p_name is None:
                                p_name = get_node_text(p_sub)
                            elif p_sub.type in ("type_annotation", "type_identifier"):
                                p_type = get_node_text(p_sub).lstrip(":").strip()
                        if p_name and p_type:
                            local_types[p_name] = p_type

                if name:
                    qname = f"{scope}.{name}" if scope else name
                    span = get_span(node)
                    kind = EntityKind.METHOD if in_class else EntityKind.FUNCTION
                    accessor_role = None
                    if t == "method_definition":
                        accessor_role = next(
                            ({"get": "getter", "set": "setter"}[child.type]
                             for child in node.children if child.type in ("get", "set")),
                            None,
                        )
                    ent_id = definition_id(qname, kind, accessor_role)
                    sl = span[0]
                    sig_line = raw_lines[sl - 1].strip() if 0 <= sl - 1 < len(raw_lines) else f"function {name}()"

                    entities.append(Entity(
                        entity_id=ent_id,
                        repo_id=repo_id,
                        file_id=file_id,
                        generation_id=generation_id,
                        name=name,
                        qualified_name=qname,
                        kind=kind,
                        span=span,
                        signature=sig_line,
                        properties={
                            "parent_id": parent_id,
                            "rel_path": rel_path,
                            **({"accessor_role": accessor_role} if accessor_role else {}),
                        }
                    ))

                    rel_id = make_relation_id(parent_id, RelationType.DEFINES.value, ent_id, file_id, span)
                    relations.append(Relation(
                        relation_id=rel_id,
                        repo_id=repo_id,
                        generation_id=generation_id,
                        source_id=parent_id,
                        target_id=ent_id,
                        rel_type=RelationType.DEFINES,
                        file_id=file_id,
                        span=span,
                        trust_class=TrustClass.EXACT
                    ))

                    if body_node:
                        extract_calls(
                            ent_id,
                            body_node,
                            enclosing_cls_id=parent_id if in_class else None,
                            enclosing_cls_name=scope.split(".")[-1] if in_class else None,
                            type_map=local_types
                        )
                # Nested JS/TS declarations stay literal-only. Their bodies are
                # excluded above rather than borrowing this callable's owner.
                continue

            elif t in ("class_declaration", "interface_declaration", "abstract_class_declaration"):
                name = None
                body_node = None
                for ch in node.named_children:
                    if ch.type in ("identifier", "type_identifier") and name is None:
                        name = get_node_text(ch)
                    elif ch.type in ("class_body", "interface_body"):
                        body_node = ch

                if name:
                    qname = f"{scope}.{name}" if scope else name
                    span = get_span(node)
                    kind = EntityKind.INTERFACE if t == "interface_declaration" else EntityKind.CLASS
                    ent_id = definition_id(qname, kind)
                    sl = span[0]
                    sig_line = raw_lines[sl - 1].strip() if 0 <= sl - 1 < len(raw_lines) else f"class {name}"

                    entities.append(Entity(
                        entity_id=ent_id,
                        repo_id=repo_id,
                        file_id=file_id,
                        generation_id=generation_id,
                        name=name,
                        qualified_name=qname,
                        kind=kind,
                        span=span,
                        signature=sig_line,
                        properties={"parent_id": parent_id, "rel_path": rel_path}
                    ))

                    rel_id = make_relation_id(parent_id, RelationType.DEFINES.value, ent_id, file_id, span)
                    relations.append(Relation(
                        relation_id=rel_id,
                        repo_id=repo_id,
                        generation_id=generation_id,
                        source_id=parent_id,
                        target_id=ent_id,
                        rel_type=RelationType.DEFINES,
                        file_id=file_id,
                        span=span,
                        trust_class=TrustClass.EXACT
                    ))

                    if body_node:
                        for b_child in reversed(body_node.named_children):
                            stack.append((b_child, qname, ent_id, True))
                continue

            elif t in ("lexical_declaration", "variable_declaration"):
                for decl in node.named_children:
                    if decl.type == "variable_declarator":
                        name_node = decl.child_by_field_name("name")
                        val_node = decl.child_by_field_name("value")
                        if (name_node is not None and val_node is not None
                                and name_node.type == "identifier"
                                and val_node.type in ("arrow_function", "function_expression")):
                            name = get_node_text(name_node)
                            if name:
                                qname = f"{scope}.{name}" if scope else name
                                span = get_span(decl)
                                ent_id = definition_id(qname, EntityKind.FUNCTION)
                                sl = span[0]
                                sig_line = raw_lines[sl - 1].strip() if 0 <= sl - 1 < len(raw_lines) else f"const {name} = () => ..."

                                entities.append(Entity(
                                    entity_id=ent_id,
                                    repo_id=repo_id,
                                    file_id=file_id,
                                    generation_id=generation_id,
                                    name=name,
                                    qualified_name=qname,
                                    kind=EntityKind.FUNCTION,
                                    span=span,
                                    signature=sig_line,
                                    properties={"parent_id": parent_id, "rel_path": rel_path}
                                ))
                                rel_id = make_relation_id(parent_id, RelationType.DEFINES.value, ent_id, file_id, span)
                                relations.append(Relation(
                                    relation_id=rel_id,
                                    repo_id=repo_id,
                                    generation_id=generation_id,
                                    source_id=parent_id,
                                    target_id=ent_id,
                                    rel_type=RelationType.DEFINES,
                                    file_id=file_id,
                                    span=span,
                                    trust_class=TrustClass.EXACT
                                ))
                                extract_calls(ent_id, val_node.child_by_field_name("body"))
                        else:
                            extract_calls(parent_id, decl)
                continue

            elif t == "method_signature":
                # An interface member is retained in its interface's literal
                # chunks; it is not a callable implementation or binding target.
                continue

            elif t == "expression_statement":
                extract_calls(parent_id, node)
                continue

        return entities, relations
