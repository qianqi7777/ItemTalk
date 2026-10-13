"""本人助手消息反馈，备注仅保留在私有表，不作为公共研究问题。"""

from app.db import one
from app.errors import DomainError, missing
from app.services.products import owned


def submit(connection, message_id, user, request):
    message = one(
        connection, "SELECT * FROM messages WHERE id=:id AND user_id=:u", id=message_id, u=user
    )
    if not message:
        missing()
    owned(connection, "conversations", message["conversation_id"], user)
    if message["role"] != "assistant":
        raise DomainError(422, "VALIDATION_ERROR", "只能评价助手的回答")
    one(
        connection,
        "INSERT INTO message_feedback(message_id,user_id,feedback_type,note"
        ") VALUES (:m,:u,:kind,:note) "
        "ON CONFLICT(message_id,user_id) DO UPDATE SET feedback_type=EXCLUD"
        "ED.feedback_type,note=EXCLUDED.note "
        "RETURNING message_id",
        m=message_id,
        u=user,
        kind=request.feedbackType,
        note=request.note,
    )
