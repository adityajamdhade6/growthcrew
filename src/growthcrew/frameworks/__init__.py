from growthcrew.frameworks.base import Framework, Point
from growthcrew.frameworks.funnel import FUNNEL
from growthcrew.frameworks.jtbd import JTBD
from growthcrew.frameworks.messaging_house import MESSAGING_HOUSE
from growthcrew.frameworks.positioning import POSITIONING
from growthcrew.frameworks.test_and_learn import TEST_AND_LEARN

# In the order the strategist applies them; each builds on the ones before.
FRAMEWORKS: tuple[Framework, ...] = (JTBD, POSITIONING, MESSAGING_HOUSE, FUNNEL, TEST_AND_LEARN)

__all__ = ["FRAMEWORKS", "Framework", "Point"]
