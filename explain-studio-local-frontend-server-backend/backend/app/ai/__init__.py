"""AI integration and content understanding package."""
from .client import (
    ModelNotFoundError,
    OllamaClient,
    OllamaConnectionError,
    OllamaInferenceError,
    OllamaTimeoutError,
)
from .gpu import gpu_manager
from .models import (
    AIAnalysisResult,
    AmbiguityItem,
    ConceptRelationship,
    Definition,
    Example,
    ImportantFact,
    LearningObjective,
    MainConcept,
    SourceReference,
    SupportingConcept,
    VisualAnalysisItem,
)

__all__ = [
    "OllamaClient",
    "OllamaConnectionError",
    "OllamaTimeoutError",
    "OllamaInferenceError",
    "ModelNotFoundError",
    "gpu_manager",
    "AIAnalysisResult",
    "MainConcept",
    "SupportingConcept",
    "ImportantFact",
    "Definition",
    "ConceptRelationship",
    "Example",
    "LearningObjective",
    "VisualAnalysisItem",
    "AmbiguityItem",
    "SourceReference",
]
