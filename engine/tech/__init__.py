from engine.tech.base import TechModel, Cost
from engine.tech.silicon_fpga import SiliconFPGA
from engine.tech.graphene_model import GrapheneWhatIf

REGISTRY = {
    "silicon_fpga": SiliconFPGA,
    "graphene_what_if": GrapheneWhatIf,
}


def get_tech(name: str) -> TechModel:
    """Return a technology model by name. Raises KeyError if unknown."""
    if name not in REGISTRY:
        raise KeyError(f"Unknown tech '{name}'. Available: {list(REGISTRY)}")
    return REGISTRY[name]()


__all__ = ["TechModel", "Cost", "SiliconFPGA", "GrapheneWhatIf",
           "REGISTRY", "get_tech"]