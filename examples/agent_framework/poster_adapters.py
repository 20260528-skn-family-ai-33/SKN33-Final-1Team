"""Team-owned poster callback entrypoint. No fabricated observations or fallback."""
from backend.agent_framework.ports import AdapterFailure


class TeamPosterInspector:
    def inspect_poster(self,request,poster):
        # poster.content contains verified normalized display bytes. Connect a
        # real inspector here; request.criteria contains the approved baseline.
        raise AdapterFailure('ADAPTER_NOT_CONFIGURED','Connect the team poster inspector before running real reviews.')


def make_inspector():return TeamPosterInspector()
