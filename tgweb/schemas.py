"""Request body models for the API endpoints."""
from typing import List, Optional

from pydantic import BaseModel


class PasswordBody(BaseModel):
    password: str = ""


class PhoneBody(BaseModel):
    phone: str = ""


class CodeBody(BaseModel):
    code: str = ""


class SessionBody(BaseModel):
    session: str = ""


class SendBody(BaseModel):
    chat_id: int
    text: str = ""
    reply_to: Optional[int] = None


class EditBody(BaseModel):
    chat_id: int
    msg_id: int
    text: str = ""


class DeleteBody(BaseModel):
    chat_id: int
    msg_ids: List[int]
    revoke: bool = True


class ProfileEditBody(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    about: Optional[str] = None


class CallbackBody(BaseModel):
    chat_id: int
    msg_id: int
    data: str = ""


class ForwardBody(BaseModel):
    from_chat_id: int
    msg_ids: List[int]
    to_chat_id: int
    hide_sender: bool = False


class ReadBody(BaseModel):
    chat_id: int
    max_id: Optional[int] = None


class TypingBody(BaseModel):
    chat_id: int


class ReactBody(BaseModel):
    chat_id: int
    msg_id: int
    emoji: str = ""


class PinBody(BaseModel):
    chat_id: int
    msg_id: int
    pinned: bool = True


class MuteBody(BaseModel):
    chat_id: int
    muted: bool = True


class ArchiveBody(BaseModel):
    chat_id: int
    archived: bool = True
