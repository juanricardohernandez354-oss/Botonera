import os
import json
import time
import threading
from datetime import datetime
import pytz
import requests
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ================= VARIABLES DE ENTORNO =================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GIST_ID = os.environ.get("GIST_ID", "")
GIST_FILENAME = os.environ.get("GIST_FILENAME", "botonera_db.json")
TIMEZONE = os.environ.get("TIMEZONE", "America/Santo_Domingo")
PORT = int(os.environ.get("PORT", 8080))

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
db_lock = threading.Lock()
user_states = {}

DAYS_NAMES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

# ================= ESTRUCTURA DE BASE DE DATOS (GIST) =================
DEFAULT_DB = {
    "config": {
        "description": "😱 <b>¡NOTICIA DE ULTIMA HORA!</b> 😱\n\n🚨 Estado: <b>Activa</b>\n⚡️ Únete a los mejores canales de Telegram:",
        "photo": None,  # Guarda el file_id o URL de la imagen
        "post_time": "14:00",  # Hora de envío (formato 24h HH:MM)
        "days": [0, 1, 2, 3, 4, 5, 6],  # 0=Lunes ... 6=Domingo
        "duration_hours": 2.0,  # Horas que dura publicada antes de auto-eliminarse
        "last_run_date": ""
    },
    "channels": [],       # Lista de dicts: {"id": -100..., "title": "...", "invite_link": "..."}
    "pending": {},        # Solicitudes pendientes: {"-100...": {"id": ..., "title": ..., "invite_link": ..., "user_id": ...}}
    "active_promo": {
        "active": False,
        "delete_at": 0,
        "messages": []    # Lista de dicts: {"chat_id": -100..., "message_id": 123}
    }
}

db = json.loads(json.dumps(DEFAULT_DB))


def load_db():
    """Carga los datos desde GitHub Gist al iniciar el bot."""
    global db
    if not GITHUB_TOKEN or not GIST_ID:
        print("⚠️ GITHUB_TOKEN o GIST_ID no configurados. Usando memoria local.")
        return
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        r = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=headers, timeout=15)
        if r.status_code == 200:
            files = r.json().get("files", {})
            if GIST_FILENAME in files and files[GIST_FILENAME].get("content"):
                loaded = json.loads(files[GIST_FILENAME]["content"])
                with db_lock:
                    for k, v in DEFAULT_DB.items():
                        if k not in loaded:
                            loaded[k] = v
                    db = loaded
                print("✅ Base de datos cargada desde GitHub Gist.")
            else:
                save_db()
        else:
            print(f"⚠️ Error al leer Gist ({r.status_code}): {r.text}")
    except Exception as e:
        print(f"⚠️ Excepción cargando Gist: {e}")


def save_db():
    """Guarda los datos inmediatamente en GitHub Gist."""
    if not GITHUB_TOKEN or not GIST_ID:
        return
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    with db_lock:
        content = json.dumps(db, indent=2, ensure_ascii=False)
    payload = {"files": {GIST_FILENAME: {"content": content}}}
    try:
        r = requests.patch(f"https://api.github.com/gists/{GIST_ID}", headers=headers, json=payload, timeout=15)
        if r.status_code != 200:
            print(f"⚠️ Error guardando Gist ({r.status_code}): {r.text}")
    except Exception as e:
        print(f"⚠️ Excepción guardando Gist: {e}")


# ================= CONSTRUCCIÓN DE LA BOTONERA =================
def build_botonera_markup():
    """Crea la botonera con el botón fijo arriba y hasta 100 botones en 2 columnas."""
    markup = InlineKeyboardMarkup(row_width=2)
    bot_username = bot.get_me().username
    add_url = f"https://t.me/{bot_username}?start=add_channel"

    # Botón obligatorio superior de ancho completo
    markup.add(InlineKeyboardButton("➕ AÑADIR MI CANAL ➕", url=add_url))

    # Botones de los canales divididos en 2 columnas (Telegram permite máx 100 botones en total por mensaje)
    channels = db["channels"][:99]
    buttons = [InlineKeyboardButton(ch["title"], url=ch["invite_link"]) for ch in channels]

    for i in range(0, len(buttons), 2):
        if i + 1 < len(buttons):
            markup.row(buttons[i], buttons[i + 1])
        else:
            markup.row(buttons[i])

    return markup


def send_botonera_to_chat(chat_id):
    """Envía el post de la botonera a un chat específico y retorna el message_id."""
    caption = db["config"]["description"]
    photo = db["config"]["photo"]
    markup = build_botonera_markup()

    if photo:
        msg = bot.send_photo(chat_id, photo=photo, caption=caption, reply_markup=markup)
    else:
        msg = bot.send_message(chat_id, text=caption, reply_markup=markup, disable_web_page_preview=True)
    return msg.message_id


def publish_botonera_all():
    """Publica la botonera en todos los canales registrados y programa su auto-eliminación."""
    if db["active_promo"]["active"]:
        delete_active_botonera()

    sent_messages = []
    for ch in list(db["channels"]):
        try:
            msg_id = send_botonera_to_chat(ch["id"])
            sent_messages.append({"chat_id": ch["id"], "message_id": msg_id})
            time.sleep(0.35)  # Evitar FloodWait de Telegram
        except Exception as e:
            print(f"⚠️ No se pudo publicar en {ch['title']} ({ch['id']}): {e}")

    duration_sec = float(db["config"]["duration_hours"]) * 3600
    db["active_promo"] = {
        "active": True,
        "delete_at": time.time() + duration_sec,
        "messages": sent_messages
    }
    save_db()
    return len(sent_messages)


def delete_active_botonera():
    """Elimina las publicaciones activas de todos los canales cuando acaba el tiempo."""
    messages = db["active_promo"].get("messages", [])
    deleted_count = 0
    for item in messages:
        try:
            bot.delete_message(item["chat_id"], item["message_id"])
            deleted_count += 1
            time.sleep(0.2)
        except Exception as e:
            print(f"⚠️ No se pudo borrar mensaje en {item['chat_id']}: {e}")

    db["active_promo"] = {
        "active": False,
        "delete_at": 0,
        "messages": []
    }
    save_db()
    return deleted_count


# ================= VERIFICACIÓN DE PERMISOS DEL CANAL =================
def verify_and_setup_channel(chat_identifier):
    """
    Verifica que el bot sea admin en el canal con permisos de gestionar mensajes
    (publicar y eliminar) e invitar usuarios mediante enlace, y crea el enlace.
    """
    try:
        chat = bot.get_chat(chat_identifier)
    except Exception:
        return False, "❌ No pude encontrar el canal. Asegúrate de haberme añadido como Administrador primero."

    if chat.type != "channel":
        return False, "❌ El chat enviado no es un canal de Telegram."

    try:
        bot_member = bot.get_chat_member(chat.id, bot.get_me().id)
    except Exception:
        return False, "❌ No pude verificar mis permisos en el canal."

    if bot_member.status != "administrator":
        return False, "❌ El bot no es Administrador en el canal."

    # Verificar permisos: Gestionar mensajes (publicar + eliminar) e Invitar con enlace
    can_post = getattr(bot_member, "can_post_messages", False)
    can_delete = getattr(bot_member, "can_delete_messages", False)
    can_invite = getattr(bot_member, "can_invite_users", False)

    if not (can_post and can_delete and can_invite):
        missing = []
        if not (can_post and can_delete):
            missing.append("• Gestionar mensajes (Publicar y Eliminar)")
        if not can_invite:
            missing.append("• Invitar usuarios mediante enlace")
        return False, "⚠️ <b>Faltan permisos obligatorios en el canal:</b>\n" + "\n".join(missing)

    try:
        invite = bot.create_chat_invite_link(chat.id, name="Botonera Promo")
        invite_link = invite.invite_link
    except Exception as e:
        return False, f"❌ Error al crear el enlace de invitación automático: {e}"

    return True, {
        "id": chat.id,
        "title": chat.title,
        "invite_link": invite_link
    }


# ================= COMANDOS Y FLUJO DE USUARIOS =================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    user_states.pop(message.from_user.id, None)
    bot_username = bot.get_me().username

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("➕ Solicitar añadir mi canal", callback_data="user_request_add"))
    if message.from_user.id == OWNER_ID:
        markup.add(InlineKeyboardButton("⚙️ Panel de Control (Owner)", callback_data="owner_panel"))

    texto = (
        f"👋 ¡Hola <b>{message.from_user.first_name}</b>!\n\n"
        f"Bienvenido al sistema de <b>Botonera Promocional</b>.\n"
        f"Aquí puedes registrar tu canal para que aparezca en nuestras rondas promocionales.\n\n"
        f"📌 <b>Requisitos para unir tu canal:</b>\n"
        f"1️⃣ Añadir a @{bot_username} como <b>Administrador</b> en tu canal.\n"
        f"2️⃣ Darle los permisos de <b>Gestionar mensajes</b> (publicar/eliminar) e <b>Invitar usuarios mediante enlace</b>.\n\n"
        f"Pulsa el botón de abajo para comenzar:"
    )
    bot.send_message(message.chat.id, texto, reply_markup=markup)


@bot.message_handler(commands=["panel", "admin"])
def cmd_panel(message):
    if message.from_user.id != OWNER_ID:
        return
    show_owner_panel(message.chat.id)


def show_owner_panel(chat_id, message_id=None):
    cfg = db["config"]
    dias_activos = ", ".join([DAYS_NAMES[d] for d in sorted(cfg["days"])]) if cfg["days"] else "Ninguno"
    estado_promo = "🟢 Publicada actualmente" if db["active_promo"]["active"] else "🔴 En espera"
    tiene_foto = "✅ Sí" if cfg["photo"] else "❌ No (Solo texto)"

    texto = (
        "⚙️ <b>PANEL DE ADMINISTRACIÓN - BOTONERA</b>\n\n"
        f"📊 <b>Canales en botonera:</b> {len(db['channels'])} / 99\n"
        f"🖼 <b>Foto configurada:</b> {tiene_foto}\n"
        f"⏰ <b>Hora de publicación:</b> {cfg['post_time']} ({TIMEZONE})\n"
        f"📅 <b>Días activos:</b> {dias_activos}\n"
        f"⏳ <b>Duración en canales:</b> {cfg['duration_hours']} horas\n"
        f"📡 <b>Estado actual:</b> {estado_promo}\n"
    )

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📝 Cambiar Descripción", callback_data="own_set_desc"),
        InlineKeyboardButton("🖼 Cambiar Foto", callback_data="own_set_photo"),
        InlineKeyboardButton("⏰ Cambiar Hora", callback_data="own_set_time"),
        InlineKeyboardButton("⏳ Cambiar Duración", callback_data="own_set_dur"),
        InlineKeyboardButton("📅 Configurar Días", callback_data="own_set_days"),
        InlineKeyboardButton("👁 Vista Previa", callback_data="own_preview"),
        InlineKeyboardButton("➕ Añadir Canal Directo", callback_data="own_add_ch"),
        InlineKeyboardButton("🗑 Quitar Canal", callback_data="own_del_ch"),
        InlineKeyboardButton("🚀 Publicar Ahora", callback_data="own_pub_now"),
        InlineKeyboardButton("🧹 Borrar Post Activo", callback_data="own_del_now")
    )

    if message_id:
        try:
            bot.edit_message_text(texto, chat_id, message_id, reply_markup=markup)
            return
        except Exception:
            pass
    bot.send_message(chat_id, texto, reply_markup=markup)


# ================= MANEJADOR DE BOTONES INLINE (CALLBACKS) =================
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    uid = call.from_user.id
    data = call.data

    # --- Solicitud de usuario para añadir canal ---
    if data == "user_request_add":
        user_states[uid] = "waiting_user_channel"
        bot_username = bot.get_me().username
        msg = (
            "📢 <b>PASOS PARA ENVIAR TU SOLICITUD:</b>\n\n"
            f"1️⃣ Entra a tu canal y añade a @{bot_username} como <b>Administrador</b>.\n"
            "2️⃣ Activa únicamente los permisos:\n"
            "   • <b>Gestionar mensajes</b> (Publicar y Eliminar mensajes)\n"
            "   • <b>Invitar usuarios mediante enlace</b>\n\n"
            "3️⃣ Una vez hecho, <b>reenvía cualquier mensaje de tu canal aquí</b> o envía el <code>@username</code> / ID (ej. <code>-100123456789</code>) de tu canal."
        )
        bot.send_message(call.message.chat.id, msg)
        bot.answer_callback_query(call.id)
        return

    # --- A partir de aquí, solo el OWNER puede usar los controles ---
    if uid != OWNER_ID:
        bot.answer_callback_query(call.id, "⛔ Solo el Owner puede hacer esto.", show_alert=True)
        return

    if data == "owner_panel":
        user_states.pop(uid, None)
        show_owner_panel(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    elif data == "own_set_desc":
        user_states[uid] = "waiting_desc"
        bot.send_message(
            call.message.chat.id,
            "📝 Envía la nueva <b>descripción</b> para la botonera (puedes usar formato HTML como <code>&lt;b&gt;negrita&lt;/b&gt;</code>):"
        )
        bot.answer_callback_query(call.id)

    elif data == "own_set_photo":
        user_states[uid] = "waiting_photo"
        bot.send_message(
            call.message.chat.id,
            "🖼 Envía la <b>foto</b> que deseas poner en la botonera, o envía la palabra <code>QUITAR</code> para dejar solo texto:"
        )
        bot.answer_callback_query(call.id)

    elif data == "own_set_time":
        user_states[uid] = "waiting_time"
        bot.send_message(
            call.message.chat.id,
            f"⏰ Envía la hora a la que se enviará la botonera en formato 24 horas <b>HH:MM</b> (Ejemplo: <code>16:30</code> o <code>09:00</code>).\nZona horaria actual: <code>{TIMEZONE}</code>"
        )
        bot.answer_callback_query(call.id)

    elif data == "own_set_dur":
        user_states[uid] = "waiting_dur"
        bot.send_message(
            call.message.chat.id,
            "⏳ Envía cuántas <b>horas</b> debe durar la publicación antes de que el bot la elimine automáticamente (Ejemplo: <code>2</code> o <code>5.5</code>):"
        )
        bot.answer_callback_query(call.id)

    elif data == "own_set_days":
        show_days_menu(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    elif data.startswith("toggle_day_"):
        day_idx = int(data.split("_")[-1])
        if day_idx in db["config"]["days"]:
            db["config"]["days"].remove(day_idx)
        else:
            db["config"]["days"].append(day_idx)
        save_db()
        show_days_menu(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "Día actualizado")

    elif data == "own_preview":
        bot.answer_callback_query(call.id, "Enviando vista previa...")
        send_botonera_to_chat(call.message.chat.id)

    elif data == "own_add_ch":
        user_states[uid] = "waiting_owner_channel"
        bot.send_message(
            call.message.chat.id,
            "➕ Asegúrate de haber puesto al bot como Admin en el canal con permisos de <b>Gestionar mensajes</b> e <b>Invitar con enlace</b>.\n\nLuego reenvía un mensaje del canal aquí o envía su <code>@username</code> / ID:"
        )
        bot.answer_callback_query(call.id)

    elif data == "own_del_ch":
        show_delete_channels_menu(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    elif data.startswith("delch_"):
        ch_id = int(data.split("_")[1])
        db["channels"] = [c for c in db["channels"] if c["id"] != ch_id]
        save_db()
        bot.answer_callback_query(call.id, "🗑 Canal eliminado de la botonera.")
        show_delete_channels_menu(call.message.chat.id, call.message.message_id)

    elif data == "own_pub_now":
        bot.answer_callback_query(call.id, "🚀 Publicando en todos los canales...")
        total = publish_botonera_all()
        bot.send_message(call.message.chat.id, f"✅ Botonera publicada exitosamente en <b>{total}</b> canales.")
        show_owner_panel(call.message.chat.id)

    elif data == "own_del_now":
        bot.answer_callback_query(call.id, "🧹 Eliminando publicaciones...")
        borrados = delete_active_botonera()
        bot.send_message(call.message.chat.id, f"🧹 Se eliminaron <b>{borrados}</b> publicaciones activas.")
        show_owner_panel(call.message.chat.id)

    # --- Aceptar o Rechazar solicitudes de usuarios ---
    elif data.startswith("approve_") or data.startswith("reject_"):
        action, ch_id_str = data.split("_", 1)
        pending_item = db["pending"].pop(ch_id_str, None)
        if not pending_item:
            bot.answer_callback_query(call.id, "Esta solicitud ya fue procesada.", show_alert=True)
            return

        if action == "approve":
            if len(db["channels"]) >= 99:
                bot.answer_callback_query(call.id, "❌ Límite de 99 canales alcanzado.", show_alert=True)
                return
            # Evitar duplicados
            db["channels"] = [c for c in db["channels"] if c["id"] != pending_item["id"]]
            db["channels"].append({
                "id": pending_item["id"],
                "title": pending_item["title"],
                "invite_link": pending_item["invite_link"]
            })
            save_db()
            bot.edit_message_text(
                f"✅ <b>Canal Aceptado:</b> {pending_item['title']}\n🔗 {pending_item['invite_link']}",
                call.message.chat.id,
                call.message.message_id
            )
            try:
                bot.send_message(
                    pending_item["user_id"],
                    f"🎉 ¡Tu solicitud para el canal <b>{pending_item['title']}</b> ha sido <b>ACEPTADA</b>! Ya forma parte de la botonera."
                )
            except Exception:
                pass
        else:
            save_db()
            bot.edit_message_text(
                f"❌ <b>Canal Rechazado:</b> {pending_item['title']}",
                call.message.chat.id,
                call.message.message_id
            )
            try:
                bot.send_message(
                    pending_item["user_id"],
                    f"❌ Tu solicitud para añadir el canal <b>{pending_item['title']}</b> fue rechazada por el administrador."
                )
            except Exception:
                pass


def show_days_menu(chat_id, message_id):
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for i, name in enumerate(DAYS_NAMES):
        status = "✅" if i in db["config"]["days"] else "❌"
        buttons.append(InlineKeyboardButton(f"{status} {name}", callback_data=f"toggle_day_{i}"))
    markup.add(*buttons)
    markup.add(InlineKeyboardButton("⬅️ Volver al Panel", callback_data="owner_panel"))
    bot.edit_message_text(
        "📅 <b>Selecciona los días en que se publicará la botonera:</b>",
        chat_id,
        message_id,
        reply_markup=markup
    )


def show_delete_channels_menu(chat_id, message_id):
    markup = InlineKeyboardMarkup(row_width=1)
    if not db["channels"]:
        markup.add(InlineKeyboardButton("⬅️ Volver al Panel", callback_data="owner_panel"))
        bot.edit_message_text("📭 No hay canales registrados actualmente.", chat_id, message_id, reply_markup=markup)
        return

    for ch in db["channels"]:
        markup.add(InlineKeyboardButton(f"🗑 {ch['title']}", callback_data=f"delch_{ch['id']}"))
    markup.add(InlineKeyboardButton("⬅️ Volver al Panel", callback_data="owner_panel"))
    bot.edit_message_text(
        "🗑 <b>Pulsa sobre un canal para quitarlo de la botonera:</b>",
        chat_id,
        message_id,
        reply_markup=markup
    )


# ================= ENTRADA DE DATOS Y MENSAJES =================
@bot.message_handler(c
