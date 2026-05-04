class ConfidenceScorer:
    """Compute confidence score for municipality resolution."""

    SIGNAL_WEIGHTS = {
        'fsa': 0.35,
        'polygon': 0.35,
        'upstream': 0.20,
        'street': 0.10,
    }

    THRESHOLDS = {
        'auto_complete': 0.85,
        'agent_review_min': 0.60,
        'human_review_max': 0.59,
    }

    def __init__(self):
        self.score = 0.0
        self.signals_fired = set()

    def add_fsa_signal(self):
        """FSA resolves to one municipality (most reliable upstream signal)."""
        self.score += self.SIGNAL_WEIGHTS['fsa']
        self.signals_fired.add('fsa')
        return self

    def add_polygon_match(self):
        """Geocoded lat/lon falls within boundary polygon."""
        self.score += self.SIGNAL_WEIGHTS['polygon']
        self.signals_fired.add('polygon')
        return self

    def add_upstream_match(self):
        """Upstream municipality matches normalized or known alias."""
        self.score += self.SIGNAL_WEIGHTS['upstream']
        self.signals_fired.add('upstream')
        return self

    def add_street_consistency(self):
        """Street address consistent with FSA."""
        self.score += self.SIGNAL_WEIGHTS['street']
        self.signals_fired.add('street')
        return self

    def apply_nominatim_conflict_penalty(self):
        """Nominatim conflicts with boundary: -0.25 penalty."""
        self.score -= 0.25
        self.score = max(0.0, self.score)  # Don't go below 0
        return self

    def get_score(self) -> float:
        """Return current confidence score."""
        return round(self.score, 2)

    def route(self) -> str:
        """Determine routing based on score."""
        score = self.get_score()
        if score >= self.THRESHOLDS['auto_complete']:
            return 'auto_complete'
        elif score >= self.THRESHOLDS['agent_review_min']:
            return 'agent_review'
        else:
            return 'human_review'
