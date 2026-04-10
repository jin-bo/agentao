# Add ACP Unit Integration And End-To-End Test Coverage

## Problem

ACP support spans protocol framing, session state, transport mapping, cancellation, permissions, and persistence. Without dedicated tests, regressions will be hard to detect.

## Scope

- Add protocol-level tests
- Add transport mapping tests
- Add end-to-end stdio tests

## Implementation Checklist

- [ ] Add `tests/test_acp_protocol.py`
- [ ] Add `tests/test_acp_initialize.py`
- [ ] Add `tests/test_acp_session_new.py`
- [ ] Add `tests/test_acp_prompt.py`
- [ ] Add `tests/test_acp_transport.py`
- [ ] Add `tests/test_acp_permissions.py`
- [ ] Add `tests/test_acp_cancel.py`
- [ ] Add `tests/test_acp_load.py`
- [ ] Add `tests/test_acp_multi_session.py`
- [ ] Add stdio subprocess end-to-end test

## Acceptance Criteria

- [ ] Core ACP flows have automated coverage
- [ ] Event mapping and permission flows are regression-tested
- [ ] Multi-session and load/cancel behavior are covered

## Dependencies

- Depends on implementation issues `01` through `12` as applicable
