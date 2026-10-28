"""NTAG216 identity check: genuine NXP, compatible clones (opt-in) and everything else."""
import pytest

from dso_enroller import ntag


class FakePn:
    """Answers GET_VERSION through InCommunicateThru like a tag would."""

    def __init__(self, version: bytes):
        self.version = version

    def call_function(self, command, params=None, response_length=0, timeout=1):
        assert command == ntag.CMD_IN_COMMUNICATE_THRU and params == [ntag.CMD_GET_VERSION]
        return bytes([0x00]) + self.version


NXP_216 = bytes([0x00, 0x04, 0x04, 0x02, 0x01, 0x00, 0x13, 0x03])
CLONE_216 = bytes([0x00, 0x53, 0x04, 0x02, 0x01, 0x00, 0x13, 0x03])       # what the college tags report
CLONE_213 = bytes([0x00, 0x53, 0x04, 0x02, 0x01, 0x00, 0x0F, 0x03])
NOT_NTAG = bytes([0x00, 0x53, 0x03, 0x01, 0x01, 0x00, 0x13, 0x03])


def test_genuine_tag_is_accepted_either_way():
    for allow in (False, True):
        v = ntag.assert_ntag216(FakePn(NXP_216), allow_clone=allow)
        assert ntag.is_genuine(v)


def test_clone_is_refused_by_default_and_accepted_when_allowed():
    with pytest.raises(ntag.TagRejected, match="clone"):
        ntag.assert_ntag216(FakePn(CLONE_216))
    assert not ntag.is_genuine(ntag.assert_ntag216(FakePn(CLONE_216), allow_clone=True))


def test_clone_allowance_does_not_weaken_the_size_and_type_checks():
    with pytest.raises(ntag.TagRejected, match="NTAG216"):
        ntag.assert_ntag216(FakePn(CLONE_213), allow_clone=True)
    with pytest.raises(ntag.TagRejected, match="not an NTAG"):
        ntag.assert_ntag216(FakePn(NOT_NTAG), allow_clone=True)
