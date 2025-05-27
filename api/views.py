import os
from django.db.models import Count, Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from history.models import ChatSession, Message
from .serializers import ChatSessionSerializer
import logging

logger = logging.getLogger(__name__)

# IMPORTS CORRETOS DA LANGCHAIN:
try:
    # Para versões novas (LangChain >= 0.1)
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
except ImportError:
    # Para versões antigas
    from langchain.schema import SystemMessage, HumanMessage, AIMessage

from langchain_openai import ChatOpenAI  # Certifique-se que este import funciona

# Inicialização do modelo OpenAI
chat = ChatOpenAI(
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.5
)

SYSTEM_PROMPT = """
Você é o IAPED, assistente pediátrico virtual da PedCare — uma equipe multidisciplinar de especialistas em saúde infantil, focada em oferecer acolhimento humano e orientação clínica de qualidade. 
Sua missão é:
1. Compreender o caso descrito pelo cuidador;
2. Fazer perguntas de triagem e aprofundamento (idade, peso, sintomas, tempo de evolução e sinais de alerta);
3. Avaliar a gravidade do quadro em etapas, identificando “red flags” (dificuldade respiratória, convulsões, sangramentos, alterações de consciência);
4. Oferecer um diagnóstico diferencial preliminar, baseado em epidemiologia e boas práticas pediátricas;
5. Orientar autocuidados (hidratação, controle de febre, cuidados respiratórios) quando seguro e
6. Sempre recomendar agendamento de consulta na PedCare (online ou domiciliar) para acompanhamento.

Diretrizes de estilo:
- Nunca mencione que é uma IA, ChatGPT ou OpenAI.
- Use tom acolhedor, empático e profissional.
- Pense em voz alta: para cada diagnóstico, explique brevemente como chegou àquela hipótese.
- A cada resposta, aguarde o cuidador antes de prosseguir.
- Se identificar sinais de emergência, oriente: “Procure socorro imediato em hospital ou posto de saúde”.
- Ao final de cada fluxo, ofereça link de agendamento da PedCare.

Fluxo sugerido (mas use flexibilidade clínica):
1. **Introdução breve**: “Olá, sou o IAPED. Como posso ajudar com a saúde do seu filho hoje?”
2. **Dados do paciente**: idade, peso, histórico imediato.
3. **Sintomas principais**: febre, tosse, vômitos, dor, irritabilidade, etc.
4. **Tempo de início & red flags**: quando começou e quaisquer sinais de gravidade.
5. **Avaliação de gravidade**:
   - **Emergência**: red flags → “Recomendo socorro imediato.”
   - **Moderado**: febre alta persistente, vômitos intensos → “Agende consulta nas próximas horas/procure na base de dados uma solução caseira”
   - **Leve**: sintomas controláveis em casa → “Podemos monitorar com cuidados caseiros. procure na base de dados uma solução caseira”
6. **Orientações de autocuidado** (se adequado):
   - Manter hidratação frequente (soro caseiro ou comercial).
   - Paracetamol/ibuprofeno conforme peso e idade.
   - Lavagem nasal com soro e ambiente arejado.
7. **Encaminhamento**:  
   “Vou compartilhar o link para agendar sua consulta na PedCare. Prefere online ou domiciliar?”
8. **Fechamento**:  
   “Resumo das orientações: [breve]. Em caso de piora, procure atendimento emergencial. Há mais algo em que eu possa ajudar?”

Sempre adapte a linguagem ao nível de entendimento do cuidador, fazendo perguntas abertas e reforçando o vínculo humano.
"""
WELCOME = "👋 Olá! Eu sou o IAPED, seu assistente pediátrico. Como posso ajudar você hoje?"

class ChatSessionViewSet(viewsets.ModelViewSet):
    queryset = ChatSession.objects.all()
    serializer_class = ChatSessionSerializer

    def get_queryset(self):
        return self.queryset.filter(user_id=self.request.user.username)

    def create(self, request, *args, **kwargs):
        logger.info(f"[CREATE] Usuário {request.user.username} requisitou nova sessão de chat.")
        force_new = request.data.get("force_new", False)

        if not force_new:
            existing = (
                ChatSession.objects
                .filter(user_id=request.user.username)
                .annotate(user_msgs=Count('messages', filter=Q(messages__role="user")))
                .filter(user_msgs=0)
                .first()
            )
            if existing:
                logger.info(f"[CREATE] Sessão existente retornada para usuário {request.user.username} (sessão {existing.id})")
                serializer = self.get_serializer(existing)
                return Response(serializer.data, status=status.HTTP_200_OK)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = serializer.save(user_id=request.user.username)
        Message.objects.create(session=session, role="assistant", content=WELCOME)
        logger.info(f"[CREATE] Nova sessão criada para usuário {request.user.username} (sessão {session.id})")

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        session = self.get_object()
        user_msg = request.data.get("message")
        if not user_msg:
            logger.warning(f"[SEND] Usuário {request.user.username} enviou mensagem vazia para sessão {session.id}")
            return Response({"detail": "Campo 'message' é obrigatório"},
                            status=status.HTTP_400_BAD_REQUEST)
        
        if len(user_msg) > 500:
            logger.warning(f"[SEND] Mensagem muito longa enviada por {request.user.username} ({len(user_msg)} chars)")
            return Response({"detail": "Mensagem muito longa (limite: 500 caracteres)."}, status=400)
        if len(user_msg) < 3:
            logger.warning(f"[SEND] Mensagem muito curta enviada por {request.user.username}")
            return Response({"detail": "Mensagem muito curta."}, status=400)

        # Salva mensagem do usuário
        Message.objects.create(session=session, role="user", content=user_msg)
        logger.info(f"[SEND] Usuário {request.user.username} enviou mensagem na sessão {session.id}")

        # Monta histórico para o modelo
        msgs = [SystemMessage(content=SYSTEM_PROMPT)]
        for m in session.messages.order_by("timestamp"):
            if m.role == "user":
                msgs.append(HumanMessage(content=m.content))
            else:
                msgs.append(AIMessage(content=m.content))

        # Chama o modelo e salva a resposta
        try:
            response = chat(messages=msgs)
            Message.objects.create(session=session, role="assistant", content=response.content)
            logger.info(f"[SEND] IA respondeu na sessão {session.id} para usuário {request.user.username}")
        except Exception as e:
            logger.error(f"[SEND][ERRO] IA falhou para usuário {request.user.username} na sessão {session.id}: {str(e)}")
            return Response({"detail": f"Erro no modelo de IA: {str(e)}"}, status=500)
        
        return Response(self.get_serializer(session).data, status=status.HTTP_200_OK)
