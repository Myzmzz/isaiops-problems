"""数据模型包.

导出所有 SQLModel 表模型，确保 SQLModel.metadata 包含全部表定义。
"""

from app.models.alert import Alert  # noqa: F401
from app.models.alert_event import AlertEvent  # noqa: F401
from app.models.alert_timeline import AlertTimeline  # noqa: F401
from app.models.problem import Problem  # noqa: F401
from app.models.problem_event import ProblemEvent  # noqa: F401
from app.models.problem_note import ProblemNote  # noqa: F401
from app.models.problem_timeline import ProblemTimeline  # noqa: F401
from app.models.silence_rule import SilenceRule  # noqa: F401
