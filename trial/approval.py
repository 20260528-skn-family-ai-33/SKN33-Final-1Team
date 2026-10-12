"""S13 인수: 내용 승인 / 출고 확정 서버 검증.

적용 규칙: B01 승인 대상 고정, B02 보류와 안내 구분, B03 미확인 정책,
B05 구버전 거절(STALE_VERSION), B08 중복 처리 키, B09 권한 우회 금지.
승인 패키지 상태: 준비 > 내용 승인 대기 > 내용 승인 > 출고 확정 / 반려 / 오래됨
"""
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field

PREPARED = "준비"
AWAITING = "내용 승인 대기"
APPROVED = "내용 승인"
RELEASED = "출고 확정"
REJECTED = "반려"
STALE = "오래됨"

ROLE_APPROVER = "approver"  # 고객 지정 승인자: 내용 승인
ROLE_PM = "pm"  # PM: 출고 확정. 승인자와 역할 분리

CLOSED_NOTICE = {"RESOLVED", "SUPERSEDED", "DISMISSED"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY, org_id TEXT NOT NULL, state_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memberships (
    order_id TEXT, user_id TEXT, org_id TEXT, role TEXT,
    PRIMARY KEY (order_id, user_id));
CREATE TABLE IF NOT EXISTS packages (
    package_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, status TEXT NOT NULL,
    target_hash TEXT NOT NULL, target_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, package_id TEXT, user_id TEXT,
    action TEXT, reason TEXT, before_status TEXT, after_status TEXT,
    target_hash TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT PRIMARY KEY, input_hash TEXT NOT NULL, result_json TEXT NOT NULL);
"""


@dataclass
class Result:
    ok: bool
    code: str = "OK"
    status: str | None = None
    reasons: list[str] = field(default_factory=list)


def target_hash(state: dict) -> str:
    """B01: revision·기준선·파일 버전/해시·QA·증빙·알림 스냅샷으로 대상을 식별한다."""
    canonical = json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def blockers(state: dict) -> list[str]:
    """B02·B03: 차단 사유만 모은다. 단순 참고 알림은 차단하지 않는다."""
    out = []
    for asset_id in state["required_assets"]:
        asset = state["assets"].get(asset_id)
        if asset is None:
            out.append(f"필수 파일 누락: {asset_id}")
        elif not asset.get("evidence_ok"):
            out.append(f"필수 증빙 누락: {asset_id}")
    if state["qa"]["status"] != "완료":
        out.append("QA 검수가 완료되지 않음")
    if state["qa"].get("stale"):
        out.append("오래된 검수 결과")
    for c in state["conditions"]:
        if not c["required"]:
            continue
        verdict = c["verdict"]
        if verdict == "미충족":
            out.append(f"필수 조건 미충족: {c['id']}")
        elif verdict == "확인 필요" and not c.get("review_done"):
            out.append(f"필수 조건 확인 필요, 담당 검토 미완료: {c['id']}")
        elif verdict == "해당 없음" and not c.get("na_basis"):
            out.append(f"해당 없음 근거 없음: {c['id']}")
        if c.get("review_required") and not c.get("review_done"):
            out.append(f"필수 검토 미완료: {c['id']}")
    for n in state["notices"]:
        if n["blocking"] and n["status"] not in CLOSED_NOTICE:
            out.append(f"차단 알림 미해결: {n['id']}")
    return sorted(set(out))


class ApprovalService:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---- 준비 단계 (테스트·데모용 시드와 패키지 생성) ----
    def seed_order(self, order_id, org_id, state, members):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO orders VALUES (?,?,?)",
                (order_id, org_id, json.dumps(state, ensure_ascii=False)))
            for user_id, user_org, role in members:
                self.conn.execute(
                    "INSERT OR REPLACE INTO memberships VALUES (?,?,?,?)",
                    (order_id, user_id, user_org, role))

    def update_order_state(self, order_id, state):
        with self.conn:
            self.conn.execute("UPDATE orders SET state_json=? WHERE order_id=?",
                              (json.dumps(state, ensure_ascii=False), order_id))

    def create_package(self, package_id, order_id) -> str:
        """현재 상태를 승인 대상으로 고정한다. 반환값이 클라이언트가 제출할 대상 해시."""
        state = self._order_state(order_id)
        h = target_hash(state)
        with self.conn:
            self.conn.execute(
                "INSERT INTO packages VALUES (?,?,?,?,?)",
                (package_id, order_id, PREPARED, h, json.dumps(state, ensure_ascii=False)))
        return h

    def request_approval(self, package_id):
        with self.conn:
            cur = self.conn.execute(
                "UPDATE packages SET status=? WHERE package_id=? AND status=?",
                (AWAITING, package_id, PREPARED))
        return cur.rowcount == 1

    # ---- 서버 검증이 필요한 두 동작 ----
    def approve_content(self, package_id, user_id, submitted_hash, process_key):
        return self._act("approve", package_id, user_id, submitted_hash, process_key,
                         role=ROLE_APPROVER, from_status=AWAITING, to_status=APPROVED)

    def confirm_release(self, package_id, user_id, submitted_hash, process_key):
        return self._act("release", package_id, user_id, submitted_hash, process_key,
                         role=ROLE_PM, from_status=APPROVED, to_status=RELEASED)

    def reject(self, package_id, user_id, submitted_hash, process_key, reason):
        if not reason.strip():
            return Result(False, "REASON_REQUIRED", reasons=["반려 사유가 필요함"])
        return self._act("reject", package_id, user_id, submitted_hash, process_key,
                         role=ROLE_APPROVER, from_status=AWAITING, to_status=REJECTED,
                         reason=reason, check_blockers=False)

    def _act(self, action, package_id, user_id, submitted_hash, process_key, *,
             role, from_status, to_status, reason="", check_blockers=True) -> Result:
        input_hash = hashlib.sha256(json.dumps(
            [action, package_id, user_id, submitted_hash, reason],
            ensure_ascii=False).encode()).hexdigest()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            result = self._act_locked(action, package_id, user_id, submitted_hash,
                                      process_key, input_hash, role, from_status,
                                      to_status, reason, check_blockers)
            self.conn.execute("COMMIT")
            return result
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def _act_locked(self, action, package_id, user_id, submitted_hash, process_key,
                    input_hash, role, from_status, to_status, reason, check_blockers):
        # B08: 같은 키·같은 입력은 기존 결과, 다른 입력은 충돌
        prev = self.conn.execute("SELECT * FROM idempotency WHERE key=?",
                                 (process_key,)).fetchone()
        if prev:
            if prev["input_hash"] != input_hash:
                return Result(False, "IDEMPOTENCY_CONFLICT",
                              reasons=["같은 처리 키에 다른 입력"])
            return Result(**json.loads(prev["result_json"]))

        pkg = self.conn.execute("SELECT * FROM packages WHERE package_id=?",
                                (package_id,)).fetchone()
        if pkg is None:
            return Result(False, "NOT_FOUND", reasons=["승인 패키지 없음"])

        # B09: 같은 조직·발주 소속 + 역할. 예외로 우회할 수 없다.
        order = self.conn.execute("SELECT * FROM orders WHERE order_id=?",
                                  (pkg["order_id"],)).fetchone()
        member = self.conn.execute(
            "SELECT * FROM memberships WHERE order_id=? AND user_id=?",
            (pkg["order_id"], user_id)).fetchone()
        if member is None or member["org_id"] != order["org_id"] or member["role"] != role:
            return Result(False, "FORBIDDEN", reasons=["해당 발주에서 이 동작을 할 권한이 없음"])

        if pkg["status"] == STALE:
            return Result(False, "STALE_VERSION", STALE,
                          ["승인 대상이 이미 오래됨. 새 패키지를 준비해야 함"])
        if pkg["status"] != from_status:
            return Result(False, "INVALID_STATE", pkg["status"],
                          [f"'{from_status}' 상태에서만 가능 (현재 '{pkg['status']}')"])

        # B05: 서버의 현재 대상과 화면이 본 대상을 모두 대조한다.
        current_hash = target_hash(json.loads(order["state_json"]))
        if submitted_hash != pkg["target_hash"] or current_hash != pkg["target_hash"]:
            self.conn.execute("UPDATE packages SET status=? WHERE package_id=?",
                              (STALE, package_id))
            self._audit(package_id, user_id, action, "STALE_VERSION",
                        pkg["status"], STALE, current_hash)
            return Result(False, "STALE_VERSION", STALE,
                          ["승인 대상이 바뀜. 이전 화면의 요청은 거절됨"])

        if check_blockers:
            found = blockers(json.loads(order["state_json"]))
            if found:
                return Result(False, "BLOCKED", pkg["status"], found)

        # 원자 저장: 상태 변경 + 이력 + 처리 키를 한 트랜잭션으로
        self.conn.execute("UPDATE packages SET status=? WHERE package_id=?",
                          (to_status, package_id))
        self._audit(package_id, user_id, action, reason, pkg["status"], to_status,
                    current_hash)
        result = Result(True, "OK", to_status)
        self.conn.execute("INSERT INTO idempotency VALUES (?,?,?)",
                          (process_key, input_hash, json.dumps(result.__dict__, ensure_ascii=False)))
        return result

    def _audit(self, package_id, user_id, action, reason, before, after, h):
        self.conn.execute(
            "INSERT INTO audit (package_id,user_id,action,reason,before_status,"
            "after_status,target_hash) VALUES (?,?,?,?,?,?,?)",
            (package_id, user_id, action, reason, before, after, h))

    def _order_state(self, order_id):
        row = self.conn.execute("SELECT state_json FROM orders WHERE order_id=?",
                                (order_id,)).fetchone()
        return json.loads(row["state_json"])

    def status(self, package_id):
        return self.conn.execute("SELECT status FROM packages WHERE package_id=?",
                                 (package_id,)).fetchone()["status"]

    def audit_log(self, package_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT user_id,action,reason,before_status,after_status FROM audit "
            "WHERE package_id=? ORDER BY id", (package_id,))]
