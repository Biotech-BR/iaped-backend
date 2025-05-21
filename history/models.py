import uuid
from django.db import models

class ChatSession(models.Model):
    """
    Representa uma sessão de chat (chat_id), atrelada a um user_id.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"ChatSession {self.id} ({self.user_id})"

class Message(models.Model):
    """
    Cada mensagem enviada ou recebida na sessão.
    """
    session = models.ForeignKey(
        ChatSession,
        related_name="messages",
        on_delete=models.CASCADE
    )
    role = models.CharField(
        max_length=16,
        choices=[
            ("system", "system"),
            ("user", "user"),
            ("assistant", "assistant")
        ]
    )
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp"]

    def __str__(self):
        return f"[{self.role}] {self.content[:50]}…"
