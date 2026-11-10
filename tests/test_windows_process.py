"""Stopping a cancelled provider's process tree on Windows, with a fake kernel."""

from __future__ import annotations

import pytest

from joinery.windows_process import WindowsProcessTree

WAIT_TIMEOUT = 0x00000102


class FakeKernel:
    def __init__(self, stubborn: set[int], immortal: frozenset[int] = frozenset()) -> None:
        self.running = set(stubborn)
        self.immortal = set(immortal)
        self.terminated: list[int] = []

    def WaitForSingleObject(self, handle: int, milliseconds: int) -> int:
        return WAIT_TIMEOUT if handle in self.running else 0

    def TerminateProcess(self, handle: int, code: int) -> bool:
        self.terminated.append(handle)
        if handle not in self.immortal:
            self.running.discard(handle)
        return True

    def CloseHandle(self, handle: int) -> bool:
        return True


def tree(kernel: FakeKernel, handles: list[int]) -> WindowsProcessTree:
    instance = WindowsProcessTree.__new__(WindowsProcessTree)
    instance.kernel = kernel
    instance.handles = list(handles)
    return instance


def test_exited_children_are_not_terminated() -> None:
    kernel = FakeKernel(stubborn=set())
    tree(kernel, [1, 2]).wait(0.01)
    assert kernel.terminated == []


def test_a_child_that_outlives_the_wait_is_terminated() -> None:
    kernel = FakeKernel(stubborn={2})
    tree(kernel, [1, 2, 3]).wait(0.01)
    assert kernel.terminated == [2]


def test_a_child_that_cannot_be_stopped_still_fails() -> None:
    kernel = FakeKernel(stubborn={2}, immortal={2})
    with pytest.raises(TimeoutError):
        tree(kernel, [1, 2]).wait(0.01)
