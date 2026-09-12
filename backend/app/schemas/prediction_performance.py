from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.prediction import EvidenceQuality


class PredictionPerformanceSampleStatus(str, Enum):
    sufficient_sample = 'sufficient_sample'
    insufficient_sample = 'insufficient_sample'


class WilsonConfidenceInterval(BaseModel):
    method: Literal['wilson'] = 'wilson'
    confidence_level: float = Field(ge=0.0, le=1.0)
    lower: float = Field(ge=0.0, le=1.0)
    upper: float = Field(ge=0.0, le=1.0)


class PredictionPerformanceMetrics(BaseModel):
    total_records: int = Field(ge=0)
    total_settled: int = Field(ge=0)
    wins: int = Field(ge=0)
    misses: int = Field(ge=0)
    win_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_interval: WilsonConfidenceInterval | None = None
    non_settled_by_status: dict[str, int] = Field(default_factory=dict)
    average_evidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    median_evidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_score_sample_size: int = Field(default=0, ge=0)
    average_odds: float | None = Field(default=None, gt=0.0)
    odds_sample_size: int = Field(default=0, ge=0)


class PredictionPerformanceGroup(BaseModel):
    key: str
    label: str
    total_settled: int = Field(ge=0)
    wins: int = Field(ge=0)
    misses: int = Field(ge=0)
    win_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_interval: WilsonConfidenceInterval | None = None
    average_evidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    median_evidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_score_sample_size: int = Field(default=0, ge=0)
    average_odds: float | None = Field(default=None, gt=0.0)
    odds_sample_size: int = Field(default=0, ge=0)
    sample_status: PredictionPerformanceSampleStatus


class PredictionProviderCalibrationBin(BaseModel):
    key: str
    label: str
    lower_bound: float = Field(ge=0.0, le=1.0)
    upper_bound_exclusive: float = Field(gt=0.0, le=1.000001)
    total_settled: int = Field(ge=0)
    wins: int = Field(ge=0)
    misses: int = Field(ge=0)
    observed_win_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    average_provider_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    sample_status: PredictionPerformanceSampleStatus


class PredictionProviderProbabilityAnalysis(BaseModel):
    probability_semantics: Literal[
        'sportybet_provider_probability',
        'not_amen_probability',
    ] = 'sportybet_provider_probability'
    valid_sample_size: int = Field(ge=0)
    missing_sample_size: int = Field(ge=0)
    wins: int = Field(ge=0)
    misses: int = Field(ge=0)
    observed_win_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    average_provider_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    brier_score: float | None = Field(default=None, ge=0.0, le=2.0)
    reliability_bins: list[PredictionProviderCalibrationBin] = Field(default_factory=list)
    sample_status: PredictionPerformanceSampleStatus


class PredictionPoolComparison(BaseModel):
    observed_win_rate_difference: float | None = Field(
        default=None, ge=-1.0, le=1.0
    )
    sample_status: PredictionPerformanceSampleStatus
    causal_claim: Literal[False] = False
    interpretation: Literal['observational_only'] = 'observational_only'


class PredictionTemporalSplit(BaseModel):
    training_settled: int = Field(ge=0)
    validation_settled: int = Field(ge=0)
    holdout_settled: int = Field(ge=0)
    training_start: datetime | None = None
    training_end: datetime | None = None
    validation_start: datetime | None = None
    validation_end: datetime | None = None
    holdout_start: datetime | None = None
    holdout_end: datetime | None = None


class PredictionLearningStatus(str, Enum):
    insufficient_sample = 'insufficient_sample'
    calibration_exploration = 'calibration_exploration'
    candidate_evaluation_ready = 'candidate_evaluation_ready'


class PredictionLearningAssessment(BaseModel):
    status: PredictionLearningStatus
    current_model: str | None = None
    settled: int = Field(ge=0)
    minimum_analysis_sample: int = Field(gt=0)
    minimum_candidate_sample: int = Field(gt=0)
    candidate_model: None = None
    active_model_changed: Literal[False] = False
    message: str
    temporal_split: PredictionTemporalSplit | None = None


class PredictionPerformanceFilters(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    model_version: str | None = None
    evidence_quality: EvidenceQuality | None = None
    competition: str | None = None


class PredictionPerformanceReport(BaseModel):
    generated_at: datetime
    date_timezone: str
    filters: PredictionPerformanceFilters
    prediction_pool: PredictionPerformanceMetrics
    qualifying_pool: PredictionPerformanceMetrics | None = None
    pool_comparison: PredictionPoolComparison | None = None
    model_versions: list[PredictionPerformanceGroup] = Field(default_factory=list)
    evidence_qualities: list[PredictionPerformanceGroup] = Field(default_factory=list)
    evidence_score_bands: list[PredictionPerformanceGroup] = Field(default_factory=list)
    odds_bands: list[PredictionPerformanceGroup] = Field(default_factory=list)
    dates: list[PredictionPerformanceGroup] = Field(default_factory=list)
    competitions: list[PredictionPerformanceGroup] = Field(default_factory=list)
    provider_probability: PredictionProviderProbabilityAnalysis
    learning: PredictionLearningAssessment
