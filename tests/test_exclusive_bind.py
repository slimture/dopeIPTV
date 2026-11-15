from __future__ import annotations

import socket

from dopeiptv.core import exclusive_bind


class _Base:
    allow_reuse_address = True

    def __init__(self):
        self.socket = self
        self.opts = []
        self.bound_with_reuse = None

    def setsockopt(self, level, opt, value):
        self.opts.append(opt)

    def server_bind(self):
        self.bound_with_reuse = self.allow_reuse_address


class _Server(exclusive_bind.ExclusiveBindMixin, _Base):
    pass


def test_on_windows_a_taken_port_is_never_shared(monkeypatch):
    monkeypatch.setattr(exclusive_bind.sys, "platform", "win32")
    monkeypatch.setattr(socket, "SO_EXCLUSIVEADDRUSE", 0x7FFF, raising=False)
    s = _Server()
    s.server_bind()
    assert s.bound_with_reuse is False and 0x7FFF in s.opts


def test_elsewhere_nothing_changes(monkeypatch):
    monkeypatch.setattr(exclusive_bind.sys, "platform", "linux")
    s = _Server()
    s.server_bind()
    assert s.bound_with_reuse is True and not s.opts
