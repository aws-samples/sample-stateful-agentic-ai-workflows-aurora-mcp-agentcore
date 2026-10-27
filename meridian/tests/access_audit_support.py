"""A stand-in for the traveler_access_audit writer that a cross-traveler 403 calls."""


class FakeAccessWriter:
    """Records the rows ``record_traveler_access`` would write, or fails like Aurora would."""

    def __init__(self, fails: bool = False) -> None:
        self.rows: list[dict] = []
        self.fails = fails

    async def record_traveler_access(self, **row):
        if self.fails:
            raise RuntimeError("Aurora is unreachable")
        self.rows.append(row)
        return "authz_test"
