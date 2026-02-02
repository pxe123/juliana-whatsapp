import os
import requests
import threading
from flask import Flask, request
from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client
from prompts.atendimento import SYSTEM_PROMPT

load_dotenv(override=True)

# Configurações de API
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()
META_TOKEN = os.getenv("META_TOKEN", "").strip()
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "").strip()
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "").strip()

# Configurações Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

client = OpenAI(api_key=OPENAI_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = Flask(__name__)

def gerenciar_memoria_supabase(whatsapp_id, nova_msg=None, role="user"):
    """Lê o histórico e salva no banco usando as colunas em português"""
    try:
        # 1. Se houver mensagem nova, salva usando os nomes do seu print
        if nova_msg:
            supabase.table("historico_mensagens").insert({
                "whatsapp_id": whatsapp_id,
                "função": role,       # Nome da coluna no seu print
                "conteúdo": nova_msg  # Nome da coluna no seu print
            }).execute()

        # 2. Busca as últimas 10 mensagens (ordenado por criado_em)
        res = supabase.table("historico_mensagens")\
            .select("função, conteúdo")\
            .eq("whatsapp_id", whatsapp_id)\
            .order("criado_em", desc=True)\
            .limit(10).execute()
        
        # 3. Formata para o padrão da OpenAI (role/content)
        historico_bd = [{"role": m["função"], "content": m["conteúdo"]} for m in reversed(res.data)]
        
        return [{"role": "system", "content": SYSTEM_PROMPT}] + historico_bd
    except Exception as e:
        print(f"❌ Erro no Banco de Dados: {e}")
        return [{"role": "system", "content": SYSTEM_PROMPT}]

def processar_e_responder(from_number, msg_text):
    try:
        # 1. Recupera histórico e salva a pergunta do usuário
        mensagens = gerenciar_memoria_supabase(from_number, msg_text, "user")

        # 2. OpenAI gera a resposta
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=mensagens
        )
        texto_ia = response.choices[0].message.content.strip()

        # 3. Salva a resposta da Juliana no banco
        gerenciar_memoria_supabase(from_number, texto_ia, "assistant")

        # 4. Envio para WhatsApp
        url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {META_TOKEN}", 
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": from_number,
            "type": "text",
            "text": {"body": texto_ia}
        }
        requests.post(url, json=payload, headers=headers)
        print(f"✅ Respondido e Salvo no Banco para {from_number}")

    except Exception as e:
        print(f"❌ Erro no processamento: {e}")

@app.route('/webhook', methods=['GET'])
def verify_token():
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    if token == VERIFY_TOKEN:
        return challenge, 200
    return 'Erro', 403

@app.route('/webhook', methods=['POST'])
def receive_message():
    data = request.get_json()
    try:
        value = data['entry'][0]['changes'][0]['value']
        
        # Ignora notificações de status (lido/entregue)
        if 'messages' not in value:
            return 'OK', 200

        msg = value['messages'][0]
        from_number = msg.get('from')
        msg_text = msg.get('text', {}).get('body', '')

        if not msg_text:
            return 'OK', 200

        # Inicia o processamento em paralelo
        threading.Thread(target=processar_e_responder, args=(from_number, msg_text)).start()

    except Exception as e:
        print(f"⚠️ Erro ao receber: {e}")

    return 'OK', 200

if __name__ == '__main__':
    # No Render, ele usará a porta que o sistema definir ou 5000 por padrão
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
