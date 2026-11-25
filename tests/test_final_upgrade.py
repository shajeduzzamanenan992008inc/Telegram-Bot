class FakeDatabase:
        def __init__(self):
            self.exported_for: int | None = None
            self.global_access_mode: str = "private"