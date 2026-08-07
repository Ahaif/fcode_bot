"""Do-nothing opponent so Step 1 replays stay readable."""
from fcode import Controller, EntityType

class Player:
    def run(self, ct: Controller) -> None:
        pass
