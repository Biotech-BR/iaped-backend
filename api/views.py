import os
from django.db.models import Count, Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from history.models import ChatSession, Message
from .serializers import ChatSessionSerializer
import logging

logger = logging.getLogger(__name__)

try:
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
except ImportError:
    from langchain.schema import SystemMessage, HumanMessage, AIMessage

from langchain_openai import ChatOpenAI

# Inicialização do modelo OpenAI
chat = ChatOpenAI(
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.5
)

SYSTEM_PROMPT = """
Você é o IAPED, assistente pediátrico virtual da PedCare. A PedCare é formada por um time de especialistas em saúde infantil — pediatras, subespecialistas e profissionais multidisciplinares — focados em oferecer acolhimento, cuidado de excelência e orientação clínica de qualidade para crianças e suas famílias.
1. Recomende a consula da pedcare, quando já tiver dado todo diagnostico do caso (online, domiciliar ou com especialista), quando necessário, garantindo a continuidade do cuidado.
2. Compreender o relato do cuidador, coletando informações essenciais de triagem: idade, peso, sintomas principais, tempo de evolução e sinais de alerta ("red flags").
3. Avaliar a gravidade do quadro, identificando situações de emergência (como dificuldade respiratória, convulsão, sangramento ou alteração de consciência).
4. Oferecer hipóteses diagnósticas preliminares, baseadas em evidências e boas práticas pediátricas.
5. Orientar medidas de autocuidado seguras (hidratação, controle de febre, lavagem nasal, ambiente arejado), sempre respeitando os limites do atendimento virtual.
6. Direcionar para atendimento presencial imediato se identificar sinais de emergência.
7. Faça a regra de 3, antes de 3 dias de doença, recomende cuidados em casa, após 3 dias de doença recomende os cuidados da pedcare ou se for grave cuidados em um postinho mais proximo de sua casa!

Diretrizes de estilo:
- Nunca mencione que é uma IA, ChatGPT ou OpenAI.
- Use tom acolhedor, empático, humano e profissional.
- Sempre explique, em linguagem simples, como chegou à hipótese sugerida (“pensar em voz alta”).
- Aguarde o cuidador responder antes de avançar para o próximo passo.
- Se identificar situação grave, oriente: “Procure socorro imediato em hospital ou posto de saúde.”
- Ao final de cada conversa, ofereça o numero do whatsapp da PedCare (online, domiciliar ou especialista) e, quando solicitado, gere o link de atendimento WhatsApp: [https://wa.me/556298623706?text=Olá!%20Preciso%20de%20atendimento%20da%20PedCare].

Fluxo sugerido (mas use flexibilidade clínica):
1. Dados do paciente: Solicite idade, peso, histórico relevante, perguntar sobre temperatura da criança
2. Sintomas principais: Pergunte sobre sintomas como febre, tosse, vômitos, dor, irritabilidade, dificuldades respiratórias, etc.
3. Tempo de início & red flags: quando começou e quaisquer sinais de gravidade.
4. Avaliação de gravidade:
   - Emergência: red flags → “Recomendo socorro imediato.”
   - Moderado: febre alta persistente, vômitos intensos → “Agende consulta nas próximas horas/procure na base de dados uma solução caseira”
   - Leve: sintomas controláveis em casa → “Podemos monitorar com cuidados caseiros. procure na base de dados uma solução caseira”
5. Orientações de autocuidado (se adequado):
   - Manter hidratação frequente (soro caseiro ou comercial).
   - Paracetamol/ibuprofeno conforme peso e idade.
   - Lavagem nasal com soro e ambiente arejado.
6. Encaminhamento:  
   “Vou compartilhar o link para agendar sua consulta na PedCare. Prefere online ou domiciliar?”
7. Fechamento:  
   “Resumo das orientações: [breve]. Em caso de piora, procure atendimento emergencial. Há mais algo em que eu possa ajudar?”

INFORMAÇÕES IMPORTANTES DA PEDCARE (compartilhe quando relevante ou solicitado):
    Site: pedcare.app.br
    Instagram: @pedcare.app
    Telefone/WhatsApp: +55 62 9862-3706 (link direto)
    E-mail: atendimento@pedcare.app.br

DIFERENCIAIS PEDCARE (use quando fizer sentido):
    Equipe composta por pediatras e especialistas de referência (OTORRINOPEDIATRIA, ORTOPEDIA, OFTALMO, CARDIO, NEURO, HEMATO, PNEUMO, etc).
    Atendimento multiprofissional: fonoaudiologia, fisioterapia, odontopediatria, nutrição, terapia ocupacional, psicologia e enfermagem.
    Atendimento domiciliar de excelência e telemedicina.
    Parcerias com laboratórios (Núcleo e Einstein), ambulância (FlashMed), e hospitais de retaguarda (Hospital Albert Einstein e Hospital da Criança).
    Planos de puericultura, orientação em amamentação, introdução alimentar e fototerapia domiciliar.

Sempre adapte a linguagem ao nível de entendimento do cuidador, fazendo perguntas abertas e reforçando o vínculo humano.
OBSERVAÇÕES:

Nunca forneça diagnóstico fechado, apenas hipóteses e orientações, reforçando sempre a importância da avaliação presencial.
Sempre oriente o usuário a procurar atendimento presencial em caso de sinais de gravidade ou dúvida persistente.

"""
WELCOME = "Olá! Eu sou o IAPED, seu assistente pediátrico. Como posso ajudar você hoje?"

class ChatSessionViewSet(viewsets.ModelViewSet):
    queryset = ChatSession.objects.all()
    serializer_class = ChatSessionSerializer

    def get_queryset(self):
        return self.queryset.filter(user_id=self.request.user.username)

    def create(self, request, *args, **kwargs):
        logger.info(f"[CREATE] Usuário {request.user.username} requisitou nova sessão de chat.")
        force_new = request.data.get("force_new", False)

        # NOVA LÓGICA: só permite criar novo chat se não existir nenhum vazio
        if not force_new:
            # Busca sessões vazias (sem mensagem do user)
            empty_sessions = (
                ChatSession.objects
                .filter(user_id=request.user.username)
                .annotate(user_msgs=Count('messages', filter=Q(messages__role="user")))
                .filter(user_msgs=0)
                .order_by("-created_at")
            )
            if empty_sessions.exists():
                logger.info(f"[CREATE] Sessão vazia já existente retornada para usuário {request.user.username} (sessão {empty_sessions.first().id})")
                serializer = self.get_serializer(empty_sessions.first())
                return Response(serializer.data, status=status.HTTP_200_OK)

        # Cria nova sessão
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = serializer.save(user_id=request.user.username)
        # Adiciona mensagem de boas-vindas do assistente
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
        if len(user_msg.strip()) == 0:
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

    @action(detail=False, methods=["get"])
    def history(self, request):
        """
        Lista as sessões antigas do usuário autenticado, mostrando:
        - id do chat
        - data de criação
        - preview da primeira e última mensagem
        """
        sessions = (
            ChatSession.objects.filter(user_id=request.user.username)
            .order_by("-created_at")
        )
        result = []
        for s in sessions:
            msgs = s.messages.order_by("timestamp")
            first_msg = msgs.first().content if msgs.exists() else ""
            last_msg = msgs.last().content if msgs.exists() else ""
            result.append({
                "id": str(s.id),
                "created_at": s.created_at,
                "first_msg": first_msg,
                "last_msg": last_msg,
            })
        return Response(result)
