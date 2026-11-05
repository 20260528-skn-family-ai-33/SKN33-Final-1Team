"""Recovery policy and canonical metadata checks without a database."""
from datetime import datetime,timezone,timedelta
import pytest
from backend.agent_framework.graph_recovery import RecoveryPolicy,content_hash,eligible,FORMAT
from backend.agent_framework.graph_monitor import save_snapshot,load_snapshot


def test_metadata_fingerprint_is_order_independent_but_detects_missing_links():
    nodes=[{'key':'a','kind':'project'},{'key':'b','sha256':'f'*64}]
    edges=[{'source':'a','target':'b','kind':'reads'}]
    assert content_hash(nodes,edges)==content_hash(list(reversed(nodes)),edges)
    assert content_hash(nodes,edges)!=content_hash(nodes,[])


@pytest.mark.parametrize('key,value',[('confirmation_seconds',0),('cooldown_seconds',True),('max_per_hour',-1),('batch_size',0)])
def test_invalid_recovery_policy(key,value):
    with pytest.raises(ValueError):RecoveryPolicy(**{key:value})


@pytest.mark.parametrize('revision,applied,observed_revision,unmanaged,expected',[
    (4,4,4,False,'consistent'),(4,3,3,False,'pending'),(4,4,None,False,'observing'),
    (4,4,5,False,'manual_review'),(4,4,4,True,'manual_review')])
def test_only_confirmed_derived_loss_can_be_rebuilt(revision,applied,observed_revision,unmanaged,expected):
    pg={'revision':revision,'applied_revision':applied,'hash':content_hash([],[])}
    observed={'revision':observed_revision,'nodes':[],'edges':[],'unmanaged':unmanaged}
    assert eligible(pg,observed)[0]==expected


def test_same_revision_but_wrong_body_is_detected_even_before_ack():
    pg={'revision':4,'applied_revision':3,'hash':content_hash([{'key':'source'}],[])}
    assert eligible(pg,{'revision':4,'nodes':[],'edges':[],'unmanaged':False})[0]=='observing'


def test_recovery_snapshot_survives_restart_and_has_separate_format(tmp_path):
    now=datetime.now(timezone.utc);path=tmp_path/'state.json'
    value={'format':FORMAT,'state':'warning','observed_at':now.isoformat(),
        'candidates':{'id':{'since':now.isoformat(),'fingerprint':'abc'}}}
    save_snapshot(path,value)
    assert load_snapshot(path,now=now,expected_format=FORMAT)==(value,True)
    assert not load_snapshot(path,now=now)[1]
    assert not load_snapshot(path,now=now+timedelta(seconds=121),max_age=120,expected_format=FORMAT)[1]
