import copy
import sqlite3
import unittest

from approval import (APPROVED, AWAITING, RELEASED, REJECTED, STALE,
                      ApprovalService)

GOOD = {
    "revision": 3,
    "baseline_version": "B-2",
    "required_assets": ["IMG-001", "VID-002"],
    "assets": {
        "IMG-001": {"version": 2, "sha256": "aa11", "evidence_ok": True},
        "VID-002": {"version": 1, "sha256": "bb22", "evidence_ok": True},
    },
    "qa": {"run_id": "QA-9", "status": "완료", "stale": False},
    "conditions": [
        {"id": "C1", "required": True, "verdict": "충족"},
        {"id": "C2", "required": True, "verdict": "확인 필요", "review_done": True},
        {"id": "C3", "required": False, "verdict": "해당 없음", "na_basis": "비대상 매체"},
    ],
    "notices": [
        {"id": "N1", "blocking": False, "status": "OPEN"},  # 참고 알림: 차단 안 함
    ],
}
MEMBERS = [("kim", "org-a", "approver"), ("park", "org-a", "pm"),
           ("evil", "org-b", "approver")]


class ApprovalTest(unittest.TestCase):
    def setUp(self):
        self.svc = ApprovalService(sqlite3.connect(":memory:", isolation_level=None))
        self.state = copy.deepcopy(GOOD)
        self.svc.seed_order("ORD-1", "org-a", self.state, MEMBERS)

    def package(self, state=None):
        if state is not None:
            self.svc.update_order_state("ORD-1", state)
        h = self.svc.create_package("PKG-1", "ORD-1")
        self.svc.request_approval("PKG-1")
        return h

    def test_happy_path_approve_then_release(self):
        h = self.package()
        r = self.svc.approve_content("PKG-1", "kim", h, "k1")
        self.assertTrue(r.ok, r)
        self.assertEqual(r.status, APPROVED)
        r = self.svc.confirm_release("PKG-1", "park", h, "k2")
        self.assertTrue(r.ok, r)
        self.assertEqual(self.svc.status("PKG-1"), RELEASED)
        self.assertEqual([a["action"] for a in self.svc.audit_log("PKG-1")],
                         ["approve", "release"])

    def test_release_requires_content_approval_first(self):
        h = self.package()
        r = self.svc.confirm_release("PKG-1", "park", h, "k1")
        self.assertEqual(r.code, "INVALID_STATE")

    def test_roles_are_separated(self):
        h = self.package()
        self.assertEqual(self.svc.approve_content("PKG-1", "park", h, "k1").code, "FORBIDDEN")
        self.svc.approve_content("PKG-1", "kim", h, "k2")
        self.assertEqual(self.svc.confirm_release("PKG-1", "kim", h, "k3").code, "FORBIDDEN")

    def test_other_org_user_is_forbidden(self):
        h = self.package()
        self.assertEqual(self.svc.approve_content("PKG-1", "evil", h, "k1").code, "FORBIDDEN")
        self.assertEqual(self.svc.status("PKG-1"), AWAITING)

    def test_stale_when_new_file_version_arrives(self):
        h = self.package()
        changed = copy.deepcopy(GOOD)
        changed["assets"]["IMG-001"] = {"version": 3, "sha256": "cc33", "evidence_ok": True}
        self.svc.update_order_state("ORD-1", changed)
        r = self.svc.approve_content("PKG-1", "kim", h, "k1")
        self.assertEqual(r.code, "STALE_VERSION")
        self.assertEqual(self.svc.status("PKG-1"), STALE)
        # 오래된 패키지는 이후에도 재사용 불가
        self.assertEqual(self.svc.approve_content("PKG-1", "kim", h, "k2").code, "STALE_VERSION")

    def test_stale_when_client_submits_old_hash(self):
        self.package()
        r = self.svc.approve_content("PKG-1", "kim", "deadbeef", "k1")
        self.assertEqual(r.code, "STALE_VERSION")

    def test_stale_blocks_release_after_approval(self):
        h = self.package()
        self.svc.approve_content("PKG-1", "kim", h, "k1")
        changed = copy.deepcopy(GOOD)
        changed["revision"] = 4
        self.svc.update_order_state("ORD-1", changed)
        self.assertEqual(self.svc.confirm_release("PKG-1", "park", h, "k2").code, "STALE_VERSION")

    def test_blockers(self):
        cases = {
            "필수 파일 누락": lambda s: s["assets"].pop("VID-002"),
            "필수 증빙 누락": lambda s: s["assets"]["IMG-001"].update(evidence_ok=False),
            "필수 조건 미충족": lambda s: s["conditions"][0].update(verdict="미충족"),
            "담당 검토 미완료": lambda s: s["conditions"][1].update(review_done=False),
            "해당 없음 근거 없음": lambda s: s["conditions"][2].pop("na_basis"),
            "오래된 검수 결과": lambda s: s["qa"].update(stale=True),
            "QA 검수가 완료되지 않음": lambda s: s["qa"].update(status="실행 중"),
            "차단 알림 미해결": lambda s: s["notices"].append(
                {"id": "N2", "blocking": True, "status": "IN_REVIEW"}),
        }
        for expected, mutate in cases.items():
            with self.subTest(expected):
                svc = ApprovalService(sqlite3.connect(":memory:", isolation_level=None))
                state = copy.deepcopy(GOOD)
                mutate(state)
                # 해당 없음은 필수 조건에만 의미가 있어 필수로 승격
                if expected == "해당 없음 근거 없음":
                    state["conditions"][2]["required"] = True
                svc.seed_order("ORD-1", "org-a", state, MEMBERS)
                h = svc.create_package("PKG-1", "ORD-1")
                svc.request_approval("PKG-1")
                r = svc.approve_content("PKG-1", "kim", h, "k1")
                self.assertEqual(r.code, "BLOCKED")
                self.assertTrue(any(expected in x for x in r.reasons), r.reasons)
                self.assertEqual(svc.status("PKG-1"), AWAITING)  # 상태 불변

    def test_resolved_blocking_notice_does_not_block(self):
        state = copy.deepcopy(GOOD)
        state["notices"].append({"id": "N2", "blocking": True, "status": "RESOLVED"})
        h = self.package(state)
        self.assertTrue(self.svc.approve_content("PKG-1", "kim", h, "k1").ok)

    def test_idempotent_retry_and_conflict(self):
        h = self.package()
        first = self.svc.approve_content("PKG-1", "kim", h, "same-key")
        again = self.svc.approve_content("PKG-1", "kim", h, "same-key")
        self.assertTrue(first.ok and again.ok)
        self.assertEqual(len(self.svc.audit_log("PKG-1")), 1)  # 이중 기록 없음
        conflict = self.svc.reject("PKG-1", "kim", h, "same-key", "다른 입력")
        self.assertEqual(conflict.code, "IDEMPOTENCY_CONFLICT")

    def test_reject_requires_reason(self):
        h = self.package()
        self.assertEqual(self.svc.reject("PKG-1", "kim", h, "k1", "  ").code, "REASON_REQUIRED")
        r = self.svc.reject("PKG-1", "kim", h, "k2", "색상 기준 불일치")
        self.assertTrue(r.ok)
        self.assertEqual(self.svc.status("PKG-1"), REJECTED)

    def test_atomic_save_rolls_back_when_audit_fails(self):
        h = self.package()

        def boom(*a, **k):
            raise RuntimeError("audit down")
        self.svc._audit = boom
        with self.assertRaises(RuntimeError):
            self.svc.approve_content("PKG-1", "kim", h, "k1")
        self.assertEqual(self.svc.status("PKG-1"), AWAITING)
        # 처리 키도 남지 않아 재시도 가능
        del self.svc._audit
        self.assertTrue(self.svc.approve_content("PKG-1", "kim", h, "k1").ok)


if __name__ == "__main__":
    unittest.main()
