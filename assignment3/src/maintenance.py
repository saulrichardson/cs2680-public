"""A route-capacity threshold and an explicit model-owned context decision."""
from dataclasses import dataclass, field


THRESHOLD = 0.80  # Schedule maintenance; do not reject input or evict history.
MAX_FAILURES = 3  # Initial invalid response plus at most two repairs.

INSTRUCTION = """[Context maintenance]
Continue the same investigation.
Prepare a working continuation account by calling compact_context(summary=...). This replaces completed conversation detail and prior summaries; it does not change source, original user instructions or current execution state. The full system instructions still apply.
Follow the summary guidance on compact_context to preserve a useful working understanding, including evidence that could change your explanation. search_history and read_file can retrieve owned archived originals; job(inspect,id) observes owned executions. Then call compact_context with your continuation summary to resume repository work. Prose alone does not complete this step."""


def capacity_scope(profile):
    """Stable route facts; current observations and request choices are not identity."""
    return {key: profile.get(key) for key in
            ('backend', 'route', 'context', 'max_output', 'input_contract', 'endpoint')} | {
                'policy': profile.get('policy', profile.get('reasoning'))}


def maintenance_capacity(profile, observation=None):
    """Choose a scheduling basis, never an input gate or an output allocation."""
    observed = observation if observation is not None else profile.get('input_capacity_observation')
    if (isinstance(observed, dict) and observed.get('scope') == capacity_scope(profile)
            and observed.get('allocation') == 'provider_default'
            and profile.get('requested_output_ceiling') is None
            and type(observed.get('tokens')) is int and observed['tokens'] > 0
            and observed.get('source')):
        return {**observed, 'basis': 'observed_default_allocation'}
    advertised = profile.get('usable_context', {})
    window = advertised.get('tokens')
    if type(window) is not int or window <= 0 or not advertised.get('source'):
        return None
    return {**advertised, 'basis': 'advertised_total_context',
            'source': advertised['source'] + '; input scheduling estimate, provider default output allocation unknown'}


@dataclass
class Maintenance:
    route: tuple | None = None
    above: bool = False
    records: list = field(default_factory=list)

    def bind_route(self, owner, profile):
        """An accepted summary consumes the scheduling basis it will resume under."""
        capacity = maintenance_capacity(profile)
        self.route = ((owner, capacity_scope(profile), capacity['basis'], capacity['tokens'])
                      if capacity is not None else None)

    def offer(self, owner, profile, measurement, *, recovering=False):
        """Assess the current rendered ordinary request, never the last receipt alone."""
        if recovering:
            return None
        capacity = maintenance_capacity(profile)
        if capacity is None:
            return None
        window = capacity.get('tokens')
        if type(window) is not int or window <= 0 or not capacity.get('source'):
            return None  # Unknown route capacity cannot establish a threshold crossing.
        route = (owner, capacity_scope(profile), capacity['basis'], window)
        if route != self.route:
            self.route, self.above = route, False
        tokens = measurement.get('estimate')
        if type(tokens) is not int or tokens < 0:
            return None
        if tokens < THRESHOLD * window:
            self.above = False
            return None
        if self.above:
            return None
        record = {'owner': owner, 'reason': 'context_threshold', 'input_measure': dict(measurement),
                  'usable_context': dict(capacity), 'threshold': THRESHOLD,
                  'threshold_tokens': THRESHOLD * window, 'status': 'offered',
                  'failed_attempts': 0, 'tool_choice': profile.get('maintenance_tool_choice', 'auto')}
        record['enforcement'] = 'provider_required_and_loop' if record['tool_choice'] == 'required' else 'loop'
        self.records.append(record)
        return record

    def finish(self):
        """Close an accepted compaction or terminal maintenance episode."""
        self.above = True

    def recover(self, owner, profile, measurement, evidence):
        """Require compaction after actual refusal without inventing a capacity."""
        self.bind_route(owner, profile)
        record = {'owner': owner, 'reason': 'provider_capacity_rejection',
                  'input_measure': dict(measurement), 'usable_context': None,
                  'threshold': None, 'threshold_tokens': None, 'status': 'offered',
                  'failed_attempts': 0, 'tool_choice': 'auto', 'enforcement': 'loop',
                  'provider_evidence': evidence}
        self.records.append(record)
        return record
