"""单轮兼容 Runtime 与显式 IterativeRuntime。"""

from .iterative import IterativeRuntime
from .single_round import SingleRoundRuntime

__all__ = ["IterativeRuntime", "SingleRoundRuntime"]
