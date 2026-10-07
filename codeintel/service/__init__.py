"""Offline CodeIntel service; provider/agent integrations are not distributed."""
from codeintel.service.production import ProductionDomainService, create_default_service
DomainService = ProductionDomainService
__all__ = ["DomainService", "ProductionDomainService", "create_default_service"]
