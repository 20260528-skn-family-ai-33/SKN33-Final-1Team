"""Strict credentials; validation errors never echo the input body."""
from pydantic import BaseModel,ConfigDict,Field,model_validator
from ..security.passwords import normalize_email,validate_password
from ..security.errors import AuthError


class Credentials(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,hide_input_in_errors=True)
    email: str | None=Field(default=None,max_length=320)
    username: str | None=Field(default=None,min_length=1,max_length=128)
    password: str=Field(min_length=1,max_length=128,repr=False)

    @model_validator(mode='after')
    def check(self):
        # Explicit null is not a way to supply both identifier fields.
        if ('email' in self.model_fields_set)==('username' in self.model_fields_set):raise ValueError('Choose one identifier.')
        try:
            validate_password(self.password)
            if 'email' in self.model_fields_set:self.email=normalize_email(self.email)
            elif self.username is None:raise AuthError('INVALID_AUTH_REQUEST',422)
        except AuthError:raise ValueError('Invalid credential structure.') from None
        return self
