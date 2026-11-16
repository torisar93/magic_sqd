"""ПК: причина отказа «pm install» без служебных строк SELinux «permissive=1» (Jetour T1, ПК 1.1.24, №16713: причиной
«Не установлено: MicroG» стояла строка «avc: denied { read } … permissive=1» — это не отказ)."""
from __future__ import annotations
from types import SimpleNamespace

import pytest

from app.install_context import AdbError, _check_pm_install_result

AVC = ("avc: denied { read } for scontext=u:r:system_server:s0 tcontext=u:object_r:fuse:s0 tclass=file "
       "permissive=1")


def _raise(text):
    with pytest.raises(AdbError) as info:
        _check_pm_install_result(SimpleNamespace(stdout=text, stderr=""))
    return str(info.value)


def test_only_permissive_avc_is_an_empty_answer():
    assert _raise(AVC + "\n") == "pm install не подтвердил успех (пустой вывод)"


def test_real_failure_is_kept_without_avc():
    assert _raise(AVC + "\nFailure [INSTALL_FAILED_INVALID_APK]\n") == "Failure [INSTALL_FAILED_INVALID_APK]"


def test_enforcing_denial_is_kept():
    enforcing = AVC.replace("permissive=1", "permissive=0")
    assert _raise(enforcing) == enforcing


def test_success_with_avc_is_success():
    _check_pm_install_result(SimpleNamespace(stdout=AVC + "\nSuccess\n", stderr=""))
