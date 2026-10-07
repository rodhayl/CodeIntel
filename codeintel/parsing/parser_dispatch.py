"""Compatibility name for the single canonical Tree-sitter parser."""

from codeintel.parsing.treesitter_parser import TreeSitterCodeParser


# Both retained import paths run the same grammar, accessor and ownership policy.
CanonicalTreeSitterCodeParser = TreeSitterCodeParser
