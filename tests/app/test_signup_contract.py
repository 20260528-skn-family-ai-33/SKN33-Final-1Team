"""Registration validation contains no database or real account inputs."""
import pytest
from pydantic import ValidationError
from backend.app.contracts.auth import Registration


def test_registration_normalizes_identity_but_preserves_password():
    value=Registration(email=' USER@Example.COM ',display_name='  개발자  ',password='  합성 비밀번호 123456789!  ')
    assert value.email=='user@example.com' and value.display_name=='개발자'
    assert value.password=='  합성 비밀번호 123456789!  '
    assert value.password not in repr(value)


@pytest.mark.parametrize('change',[
    {'email':'bad'}, {'display_name':'   '}, {'display_name':'x\x00y'},
    {'display_name':'x'*101}, {'password':'weak-secret'}, {'password':'x'*129},
    {'password':'12345678901234\x00'}, {'account_kind':'developer'},
    {'is_active':True}, {'role':'owner'}, {'password_hash':'untrusted'},
])
def test_registration_rejects_invalid_values_and_privilege_fields(change):
    body={'email':'synthetic@example.com','display_name':'개발자','password':'synthetic-password-123',**change}
    with pytest.raises(ValidationError) as failure:Registration.model_validate(body)
    assert body['password'] not in str(failure.value)
