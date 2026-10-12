"""1건 시나리오: 승인 대기 중 새 파일 버전이 들어와 이전 화면의 승인이 거절되는 흐름."""
import copy
import sqlite3

from test_approval import GOOD, MEMBERS
from approval import ApprovalService

svc = ApprovalService(sqlite3.connect(":memory:", isolation_level=None))
svc.seed_order("ORD-1", "org-a", copy.deepcopy(GOOD), MEMBERS)
h = svc.create_package("PKG-1", "ORD-1")
svc.request_approval("PKG-1")
print("1) 패키지 상태:", svc.status("PKG-1"), "| 대상 해시:", h[:12])

print("2) PM이 내용 승인 시도 ->", svc.approve_content("PKG-1", "park", h, "d1"))

newer = copy.deepcopy(GOOD)
newer["assets"]["IMG-001"] = {"version": 3, "sha256": "cc33", "evidence_ok": True}
svc.update_order_state("ORD-1", newer)
print("3) 제작자가 IMG-001 v3 제출 (승인 화면은 아직 이전 대상)")

print("4) 승인자가 이전 화면에서 승인 ->", svc.approve_content("PKG-1", "kim", h, "d2"))
print("   패키지 상태:", svc.status("PKG-1"))

h2 = svc.create_package("PKG-2", "ORD-1")
svc.request_approval("PKG-2")
print("5) 새 패키지로 승인 ->", svc.approve_content("PKG-2", "kim", h2, "d3"))
print("6) PM 출고 확정 ->", svc.confirm_release("PKG-2", "park", h2, "d4"))
print("이력:", *svc.audit_log("PKG-2"), sep="\n  ")
