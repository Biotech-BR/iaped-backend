import os
from django.db.models import Count, Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from history.models import ChatSession, Message
from .serializers import ChatSessionSerializer

# Prompts
SYSTEM_PROMPT = """
Você é o IAPED, assistente pediátrico virtual da PedCare — uma equipe multidisciplinar de especialistas em saúde infantil, focada em oferecer acolhimento humano e orientação clínica de qualidade. 
Sua missão é:
1. Compreender o caso descrito pelo cuidador;
2. Fazer perguntas de triagem e aprofundamento (idade, peso, sintomas, tempo de evolução e sinais de alerta);
3. Avaliar a gravidade do quadro em etapas, identificando “red flags”;
4. Oferecer diagnóstico diferencial preliminar;
5. Orientar autocuidados quando seguro;
6. Recomendar agendamento de consulta na PedCare.
"""
WELCOME = "👋 Olá! Eu sou o IAPED, seu assistente pediátrico. Como posso ajudar você hoje?"

from langchain_openai import ChatOpenAI  # se estiver usando langchain-openai
from langchain.schema import SystemMessage, HumanMessage, AIMessage

chat = ChatOpenAI(
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.5
)

class ChatSessionViewSet(viewsets.ModelViewSet):
    queryset = ChatSession.objects.all()
    serializer_class = ChatSessionSerializer

    def get_queryset(self):
        return self.queryset.filter(user_id=self.request.user.username)

    def create(self, request, *args, **kwargs):
        # Reutiliza sessão vazia se existir
        existing = (
            ChatSession.objects
            .filter(user_id=request.user.username)
            .annotate(user_msgs=Count('messages', filter=Q(messages__role="user")))
            .filter(user_msgs=0)
            .first()
        )
        if existing:
            return Response(self.get_serializer(existing).data, status=status.HTTP_200_OK)
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        session = serializer.save(user_id=self.request.user.username)
        # Limpa outras sessões vazias
        (
            ChatSession.objects
            .filter(user_id=self.request.user.username)
            .annotate(user_msgs=Count('messages', filter=Q(messages__role="user")))
            .filter(user_msgs=0)
            .exclude(id=session.id)
            .delete()
        )
        # Mensagem de boas‑vindas
        Message.objects.create(session=session, role="assistant", content=WELCOME)

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        session = self.get_object()
        user_msg = request.data.get("message")
        if not user_msg:
            return Response({"detail": "Campo 'message' é obrigatório"},
                            status=status.HTTP_400_BAD_REQUEST)

        # Salva mensagem do usuário
        Message.objects.create(session=session, role="user", content=user_msg)

        # Monta histórico para o modelo
        msgs = [SystemMessage(content=SYSTEM_PROMPT)]
        for m in session.messages.order_by("timestamp"):
            if m.role == "user":
                msgs.append(HumanMessage(content=m.content))
            else:
                msgs.append(AIMessage(content=m.content))

        # Chama o modelo e salva a resposta
        response = chat(messages=msgs)
        Message.objects.create(session=session, role="assistant", content=response.content)

        return Response(self.get_serializer(session).data, status=status.HTTP_200_OK)
