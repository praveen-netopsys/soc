"""Structured Pydantic models for AI Content Understanding and Grounding."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class SourceReference(BaseModel):
    """Traceable provenance link pointing back to raw input material.
    
    source_id: Original uploaded file identifier (e.g. source UUID, filename, or 'pasted_text').
    content_block_id: Individual extracted content block identifier within that source.
    """
    source_id: str = Field(description="Unique ID of the original source file or pasted text")
    original_filename: str = Field(default="", description="Filename of the source input")
    slide_number: int | None = Field(default=None, description="Slide index if from a PPTX file")
    content_block_id: str | None = Field(default=None, description="Specific extracted content block ID")
    asset_reference: str | None = Field(default=None, description="Relative path of visual asset if applicable")


class MainConcept(BaseModel):
    """Core educational concept extracted from the content."""
    name: str = Field(description="Title or name of the main concept")
    explanation: str = Field(description="Clear, accessible explanation of the concept")
    importance: str = Field(description="Why this concept is fundamental to the topic")
    source_references: list[SourceReference] = Field(
        default_factory=list,
        description="Source blocks supporting this concept",
    )


class SupportingConcept(BaseModel):
    """Secondary concept that elaborates or reinforces main concepts."""
    name: str = Field(description="Name of the supporting concept")
    explanation: str = Field(description="Detailed explanation")
    source_references: list[SourceReference] = Field(
        default_factory=list,
        description="Source blocks supporting this concept",
    )


class ImportantFact(BaseModel):
    """Specific factual claim, data point, or principle extracted from the material."""
    statement: str = Field(description="The concrete factual claim or datum")
    context: str | None = Field(default=None, description="Educational context or relevance")
    source_references: list[SourceReference] = Field(
        default_factory=list,
        description="Exact source blocks grounding this fact",
    )


class Definition(BaseModel):
    """Terminology or formal definition established in the source material."""
    term: str = Field(description="Term or phrase being defined")
    definition: str = Field(description="Precise educational definition")
    source_references: list[SourceReference] = Field(
        default_factory=list,
        description="Source grounding for this definition",
    )


class ConceptRelationship(BaseModel):
    """Explicit semantic relationship connecting two concepts."""
    from_concept: str = Field(description="Subject concept")
    to_concept: str = Field(description="Target concept")
    relationship_type: str = Field(
        description="e.g., prerequisite_of, part_of, causes, contrasts_with, applies_to"
    )
    description: str = Field(description="Brief explanation of how they relate")


class Example(BaseModel):
    """Concrete example or use-case illustrating a concept."""
    concept: str = Field(description="Concept being illustrated")
    description: str = Field(description="The example scenario, application, or case study")
    source_references: list[SourceReference] = Field(
        default_factory=list,
        description="Source grounding for this example",
    )


class LearningObjective(BaseModel):
    """Clear educational outcome the learner should achieve."""
    objective: str = Field(description="Actionable learning objective (e.g., 'Explain the mechanism of...')")
    blooms_level: str | None = Field(
        default="understand",
        description="Cognitive level: remember, understand, apply, analyze, evaluate, create",
    )


class VisualAnalysisItem(BaseModel):
    """Vision model interpretation of an image, chart, or diagram."""
    source_id: str
    original_filename: str
    slide_number: int | None = None
    content_block_id: str | None = None
    asset_reference: str
    visual_type: str = Field(
        description="Type: diagram, chart, illustration, screenshot, photo, table_image, other"
    )
    description: str = Field(description="Objective description of visual contents")
    extracted_text: str | list[str] | None = Field(
        default=None,
        description="Visible labels or text in the visual (string or list of strings)",
    )
    key_takeaways: list[str] = Field(
        default_factory=list,
        description="Educational takeaways visually conveyed by this asset",
    )
    relevance_to_topic: str | None = Field(
        default=None,
        description="How this visual contributes to understanding the subject",
    )


class AmbiguityItem(BaseModel):
    """Identified ambiguity, contradiction, or missing information in source material."""
    topic: str = Field(description="Topic or area with incomplete context")
    issue: str = Field(description="Nature of the ambiguity or discrepancy")
    missing_information: str = Field(description="What extra information would be needed")


class AIAnalysisResult(BaseModel):
    """Complete structured analysis manifest saved per job."""
    job_id: str
    analyzed_at: str
    title: str = Field(description="Extracted or inferred document/lesson title")
    subject: str = Field(description="Primary academic or educational discipline/subject")
    summary: str = Field(description="Comprehensive educational summary of the material")
    main_concepts: list[MainConcept] = Field(default_factory=list)
    supporting_concepts: list[SupportingConcept] = Field(default_factory=list)
    important_facts: list[ImportantFact] = Field(default_factory=list)
    definitions: list[Definition] = Field(default_factory=list)
    concept_relationships: list[ConceptRelationship] = Field(default_factory=list)
    examples: list[Example] = Field(default_factory=list)
    prerequisites: list[str] = Field(
        default_factory=list,
        description="Prior knowledge or concepts needed to understand this material",
    )
    learning_objectives: list[LearningObjective] = Field(default_factory=list)
    ambiguities_and_gaps: list[AmbiguityItem] = Field(default_factory=list)
    visual_analyses: list[VisualAnalysisItem] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ─── Educational Planner Models ───────────────────────────────────────────────

class TeachingModule(BaseModel):
    """An individual instructional module or training lesson in the educational plan."""
    module_id: str = Field(description="Unique ID for module (e.g. mod_1)")
    title: str = Field(description="Module title / topic focus")
    purpose: str = Field(description="Pedagogical objective of this section")
    target_concepts: list[str] = Field(default_factory=list, description="Concepts introduced or deepened")
    prerequisite_bridges: list[str] = Field(default_factory=list, description="Background knowledge bridged here")
    key_analogies: list[str] = Field(default_factory=list, description="Illustrative metaphors or analogies")
    depth_level: str = Field(default="introductory", description="introductory | in-depth | rigorous")
    estimated_duration_seconds: float = Field(default=30.0, description="Target duration for this module")
    start_slide: int = Field(default=1, description="Starting slide index")
    end_slide: int = Field(default=1, description="Ending slide index")
    slide_numbers: list[int] = Field(default_factory=list, description="Slide numbers covered by this lesson")
    learning_objective: str = Field(default="", description="Core practical learning objective")
    important_facts: list[str] = Field(default_factory=list, description="Key rules, standards or procedures")
    render_status: str = Field(default="pending", description="pending | rendering | completed | failed")
    output_video_path: str | None = Field(default=None, description="Path to rendered individual lesson video MP4")


class EducationalPlan(BaseModel):
    """Complete pedagogical architecture for the lesson."""
    job_id: str
    audience_level: str = Field(description="beginner | intermediate | advanced")
    pedagogical_approach: str = Field(description="Core educational methodology")
    pacing_strategy: str = Field(description="Pacing guidance based on concept complexity")
    modules: list[TeachingModule] = Field(default_factory=list)
    total_estimated_seconds: float = Field(default=0.0)


# ─── Narration Script Generator Models ────────────────────────────────────────

class ScriptSegment(BaseModel):
    """Spoken line or narrative paragraph paired with an instructional scene."""
    segment_id: str = Field(description="e.g. seg_1")
    scene_id: str = Field(description="Corresponding storyboard scene ID")
    module_id: str = Field(description="Associated educational module ID")
    speaker_tone: str = Field(default="engaging", description="Tone: engaging | deliberate | explanatory")
    narration_text: str = Field(description="Exact spoken narration text")
    word_count: int = Field(default=0)
    estimated_duration_seconds: float = Field(default=0.0)
    source_references: list[SourceReference] = Field(default_factory=list)


class NarrationScript(BaseModel):
    """Full conversational teaching script."""
    job_id: str
    title: str
    target_audience: str
    total_word_count: int = 0
    estimated_reading_time_seconds: float = 0.0
    segments: list[ScriptSegment] = Field(default_factory=list)


# ─── Storyboard Generator Models ──────────────────────────────────────────────

class StoryboardScene(BaseModel):
    """An executable scene specification ready for Motion Canvas or Manim."""
    scene_id: str = Field(description="Unique scene identifier e.g. scene_1")
    module_id: str = Field(description="Module ID")
    scene_purpose: str = Field(
        description="hook | concept_introduction | mechanism_breakdown | example_walkthrough | summary_recap"
    )
    narration_text: str = Field(description="Voiceover narration for this scene")
    visual_description: str = Field(description="Detailed visual staging and coordinate guidance")
    animation_type: str = Field(
        description="motion_canvas_code | manim_math | kinetic_typography | diagram_reveal | concept_map | comparison"
    )
    on_screen_text: list[str] = Field(default_factory=list, description="Titles, equations, labels")
    visual_asset_references: list[str] = Field(default_factory=list, description="Relative paths to extracted images")
    source_references: list[SourceReference] = Field(default_factory=list)
    estimated_visual_duration: float = Field(default=10.0, description="Duration in seconds")
    transition_instructions: str = Field(default="fade", description="fade | slide_left | zoom_in | dissolve | match_cut")
    key_points: list[str] = Field(default_factory=list, description="Specific factual bullet points and rules from presentation slide")
    key_metric: str = Field(default="", description="Key quantitative metric or threshold e.g. 15 km/h, 30s")
    takeaway: str = Field(default="", description="Practical takeaway or rule summary")
    layout_type: str = Field(default="checklist_walkthrough", description="Visual infographic layout archetype")
    image_path: str = Field(default="", description="Absolute path to local or downloaded internet reference image")
    visual_theme: str = Field(
        default="auto",
        description="Dynamic visual theme: safety_high_vis | warehouse_logistics | plant_manufacturing | environmental_zeroharm | emergency_critical_loto | corporate_compliance",
    )
    character_role: str = Field(default="worker", description="worker | driver | supervisor | visitor")
    character_action: str = Field(default="inspect", description="walk | point | inspect | stop | clean")
    equipment_type: str = Field(default="forklift", description="forklift | prime_mover | racks | workbench_5s | loto_station | visitor_kiosk | none")
    environment_type: str = Field(default="auto", description="workstation_5s | highway_road | visitor_gate | critical_loto | warehouse_dock | auto")


class Storyboard(BaseModel):
    """Structured scene-by-scene storyboard executable by rendering engines."""
    job_id: str
    title: str
    total_scenes: int = 0
    estimated_total_duration_seconds: float = 0.0
    scenes: list[StoryboardScene] = Field(default_factory=list)


# ─── Adaptive Duration Models ─────────────────────────────────────────────────

class SceneDurationBreakdown(BaseModel):
    """Detailed runtime breakdown for an individual scene."""
    scene_id: str = Field(description="Scene identifier")
    narration_duration_seconds: float = Field(description="Duration of spoken narration")
    visual_demonstration_duration_seconds: float = Field(default=0.0, description="Visual action/demo duration")
    cognitive_pause_seconds: float = Field(default=0.0, description="Deliberate pause for concept retention")
    reading_time_seconds: float = Field(default=0.0, description="Extra reading time not absorbed during speech")
    transition_duration_seconds: float = Field(default=0.75, description="Scene transition padding")
    total_scene_duration_seconds: float = Field(description="Calculated total runtime for this scene")


class ModuleDurationReconciliation(BaseModel):
    """Reconciliation between pedagogical teaching-plan module and actual scene runtimes."""
    module_id: str = Field(description="Teaching module identifier")
    title: str = Field(description="Module title")
    planned_module_duration_seconds: float = Field(description="Initial estimated duration from educational plan")
    actual_calculated_duration_seconds: float = Field(description="Real calculated duration from constituent scenes")
    reconciliation_note: str = Field(description="Explicit explanation of any runtime difference")


class DurationEstimate(BaseModel):
    """Adaptive video duration calculated from narration, visuals, pauses, and pacing."""
    total_duration_seconds: float
    formatted_duration: str
    narration_duration_seconds: float
    visual_demonstration_duration_seconds: float = 0.0
    cognitive_pause_seconds: float = 0.0
    reading_time_not_already_accounted_for_seconds: float = 0.0
    transition_duration_seconds: float = 0.0
    # Backward-compatible fields
    visual_reading_pause_seconds: float = 0.0
    transition_padding_seconds: float = 0.0
    words_per_minute: float = 135.0
    total_words: int = 0
    pacing_note: str
    scene_breakdowns: list[SceneDurationBreakdown] = Field(default_factory=list)
    module_reconciliations: list[ModuleDurationReconciliation] = Field(default_factory=list)
    measured_audio_duration_override: float | None = None


# ─── Quality Assurance Layer Models ───────────────────────────────────────────

class QualityReport(BaseModel):
    """Automated programmatic verification of director outputs."""
    overall_score: float = Field(description="Score between 0.0 and 1.0")
    passed: bool = Field(description="True if all critical checks passed and rendering may proceed")
    source_coverage_pct: float = Field(description="Percentage of sources referenced")
    total_claims_grounded: int = Field(default=0)
    invalid_source_references: list[str] = Field(default_factory=list)
    mismatched_source_references: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_concepts: list[str] = Field(default_factory=list)
    duplicate_explanations: list[str] = Field(default_factory=list)
    script_completeness_check: bool = True
    storyboard_consistency_check: bool = True
    actionable_reasons: list[str] = Field(default_factory=list, description="Specific actionable reasons if QA failed")
    warnings: list[str] = Field(default_factory=list)


# ─── Complete Director Result ─────────────────────────────────────────────────

class CompleteDirectorResult(BaseModel):
    """Unified master package returned by the AI Educational Director."""
    job_id: str
    analyzed_at: str
    analysis: AIAnalysisResult
    plan: EducationalPlan
    script: NarrationScript
    storyboard: Storyboard
    duration: DurationEstimate
    quality: QualityReport

