"""영상 로고 검수: 후보 찾기 직후 사람 확인 없이 정밀 대조할 묶음을 고르는 규칙(routes.auto_selection·auto_answers)."""
from backend.app.video.logos.routes import auto_answers
from backend.app.video.logos.routes import auto_selection


def group(rank, sec=1, box=(10.4, 20.5, 60.5, 80.2)):
    return {"secs": [sec], "rank": list(rank), "rep": [[sec, list(box)] for _ in rank], "box": [{} for _ in rank]}


def test_picks_top_group_and_close_links_up_to_three():
    groups = [group((0.50, 0.10)), group((0.95, 0.20)), group((0.90, 0.30)), group((0.88, 0.05)), group((0.87, 0.01))]
    picks = auto_selection(groups, 2)
    # 원본 로고 0: 1위(0.95)와 그 92%(0.874) 이상인 묶음을 점수 순으로 최대 3개
    assert picks[0] == {"logo": 0, "decision": "done", "groups": [1, 2, 3]}
    # 원본 로고 1: 1위(0.30)의 92%(0.276) 이상은 1위뿐
    assert picks[1] == {"logo": 1, "decision": "done", "groups": [2]}


def test_no_candidates_or_non_positive_top_score():
    assert auto_selection([], 1) == [{"logo": 0, "decision": "none", "groups": []}]
    # 1위 점수가 0 이하이면 같은 로고 연결 후보를 붙이지 않고 1위만 대조한다
    assert auto_selection([group((-0.2,)), group((-0.3,))], 1)[0]["groups"] == [0]


def test_answers_match_screen_handoff_shape():
    groups = [group((0.9,), sec=4), group((0.1,), box=(5, 5, 5, 9))]
    picks = [{"logo": 0, "decision": "done", "groups": [0, 1]}]
    [answer] = auto_answers(picks, groups, [{"name": "주최 로고"}])
    # 화면 handoff처럼 Math.round(.5는 올림)로 좌표를 정수로 만들고, 넓이가 없는 상자는 빼고 보낸다
    assert answer == {"logo": 0, "reference": "주최 로고", "decision": "done",
                      "crops": [{"group": 0, "sec": 4, "bbox": [10, 21, 61, 80], "source": "candidate"}]}
    assert auto_answers([{"logo": 0, "decision": "none", "groups": []}], groups, [{"name": "x"}])[0]["decision"] == "none"
