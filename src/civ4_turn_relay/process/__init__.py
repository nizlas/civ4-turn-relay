"""Process adapter: Civ launch planning, supervision port, Windows backend."""

from civ4_turn_relay.process.fake import FakeProcessSupervisor
from civ4_turn_relay.process.launch_config import (
    CivLaunchCommand,
    CivLaunchConfiguration,
    LaunchPlan,
    LaunchPlanOutcome,
    build_civ_command,
    build_launch_plan,
)
from civ4_turn_relay.process.port import (
    CloseRequestOutcome,
    CloseRequestResult,
    FocusOutcome,
    FocusResult,
    LaunchOutcome,
    LaunchResult,
    ProbeOutcome,
    ProbeResult,
    ProcessIdentity,
    ProcessSupervisor,
    SupervisorAvailability,
    TerminateOutcome,
    TerminateResult,
    observation_from_identity,
)
from civ4_turn_relay.process.windows import (
    ProcessInfo,
    RealWindowsBackend,
    WindowsProcessBackend,
    WindowsProcessSupervisor,
)

__all__ = [
    "CivLaunchCommand",
    "CivLaunchConfiguration",
    "CloseRequestOutcome",
    "CloseRequestResult",
    "FakeProcessSupervisor",
    "FocusOutcome",
    "FocusResult",
    "LaunchOutcome",
    "LaunchPlan",
    "LaunchPlanOutcome",
    "LaunchResult",
    "ProbeOutcome",
    "ProbeResult",
    "ProcessIdentity",
    "ProcessInfo",
    "ProcessSupervisor",
    "RealWindowsBackend",
    "SupervisorAvailability",
    "TerminateOutcome",
    "TerminateResult",
    "WindowsProcessBackend",
    "WindowsProcessSupervisor",
    "build_civ_command",
    "build_launch_plan",
    "observation_from_identity",
]
