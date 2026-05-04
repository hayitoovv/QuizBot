import telebot
from telebot import types
from docx import Document
import random
import time
import threading

TOKEN = "8475185063:AAEUpGMnfmbrV0SUNRR-egn-Rq60wcooXI0"
bot = telebot.TeleBot(TOKEN, num_threads=8)


# ====== 429 (rate limit) retry — Telegram API call'lariga avtomat o'rab qo'yamiz ======
def _retry_429(fn, *args, **kwargs):
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 429:
                wait = 1
                try:
                    wait = e.result_json.get("parameters", {}).get("retry_after", 1)
                except Exception:
                    pass
                print(f"⏳ 429 rate-limit, {wait}s kutilmoqda…")
                time.sleep(wait + 0.5)
                continue
            raise
    return None


for _name in ("send_message", "send_poll", "edit_message_text",
              "edit_message_reply_markup", "answer_callback_query"):
    _orig = getattr(bot, _name)
    def _wrap(orig):
        def caller(*a, **kw):
            return _retry_429(orig, *a, **kw)
        return caller
    setattr(bot, _name, _wrap(_orig))


TESTS = {
    "btp": {
        "name": "Boshlang'ich ta'lim pedagogikasi",
        "file": "Boshlang'ich ta'lim pedagogikasi.docx",
    },
}

CHUNK_SIZES = {"20": 20, "50": 50, "100": 100}
TIMER_OPTIONS = [30, 20, 10]
REVEAL_DELAY = 2  # poll yopilgandan keyin keyingi savolga o'tishdan oldin pauza

chat_session = {}     # chat_id -> session dict
poll_to_chat = {}     # poll_id -> chat_id (poll_answer handler uchun)

_bot_username = None
def bot_username():
    global _bot_username
    if _bot_username is None:
        _bot_username = bot.get_me().username
    return _bot_username


# ====== DOCX parser ======
# Format:
#   Savol matni
#   ====
#   # To'g'ri variant   (# belgisi to'g'ri javobni bildiradi)
#   ====
#   Variant 2
#   ====
#   Variant 3
#   ====
#   Variant 4
#   ++++   (savollar oraliq ajratgich; oxirgi savolda bo'lmasligi mumkin)
def load_quiz_from_docx(file_path):
    try:
        document = Document(file_path)
    except Exception as e:
        print(f"❌ Faylni o'qishda xato: {e}")
        return []

    lines = []
    for p in document.paragraphs:
        text = p.text.replace("\xa0", " ").strip()
        if text:
            lines.append(text)

    blocks = [[]]
    for line in lines:
        if line.startswith("++++"):
            blocks.append([])
        else:
            blocks[-1].append(line)

    quiz_data = []
    for block in blocks:
        if not block:
            continue
        sections = [[]]
        for line in block:
            if line.startswith("===="):
                sections.append([])
            else:
                sections[-1].append(line)
        question = " ".join(sections[0]).strip()
        if not question:
            continue
        variants_raw = [" ".join(s).strip() for s in sections[1:] if any(s)]
        if len(variants_raw) < 2:
            continue
        correct_index = 0
        variants = []
        for i, v in enumerate(variants_raw):
            if v.startswith("#"):
                correct_index = i
                v = v[1:].strip()
            variants.append(v)
        quiz_data.append({
            "savol": question,
            "variantlar": variants,
            "javob_index": correct_index,
        })
    return quiz_data


_quiz_cache = {}

def get_quiz_template(test_id):
    if test_id not in _quiz_cache:
        _quiz_cache[test_id] = load_quiz_from_docx(TESTS[test_id]["file"])
    return _quiz_cache[test_id]


def shuffled_question(q):
    combined = list(enumerate(q["variantlar"]))
    random.shuffle(combined)
    return {
        "savol": q["savol"],
        "variantlar": [v for _, v in combined],
        "javob_index": next(j for j, (orig, _) in enumerate(combined) if orig == q["javob_index"]),
    }


def build_session_quiz(test_id, mode, start):
    template = get_quiz_template(test_id)
    total = len(template)
    if mode == "full":
        indexes = list(range(total))
    elif mode == "rand":
        n = min(30, total)
        indexes = sorted(random.sample(range(total), n))
    else:
        chunk = CHUNK_SIZES[mode]
        end = min(start + chunk, total)
        indexes = list(range(start, end))
    return [shuffled_question(template[i]) for i in indexes]


def subset_label(mode, start, count):
    if mode == "full":
        return f"To'liq test (1–{count})"
    if mode == "rand":
        return f"Tasodifiy {count} ta savol"
    return f"{start + 1}–{start + count}-savollar"


# ====== Klaviaturalar ======

def kb_main_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    for tid, info in TESTS.items():
        total = len(get_quiz_template(tid))
        markup.add(types.InlineKeyboardButton(
            f"📘 {info['name']} [{total}]", callback_data=f"sel:{tid}"
        ))
    return markup


def kb_mode_menu(test_id):
    total = len(get_quiz_template(test_id))
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton(
        f"📚 To'liq test ({total} ta)", callback_data=f"mode:{test_id}:full"
    ))
    if total >= 5:
        markup.add(types.InlineKeyboardButton(
            "🎯 Tasodifiy 30 ta", callback_data=f"mode:{test_id}:rand"
        ))
    for size_key, size in CHUNK_SIZES.items():
        if total >= size:
            markup.add(types.InlineKeyboardButton(
                f"📑 {size} talik bo'lim", callback_data=f"mode:{test_id}:{size_key}"
            ))
    markup.add(types.InlineKeyboardButton("« Orqaga", callback_data="back:main"))
    return markup


def kb_range_menu(test_id, mode):
    total = len(get_quiz_template(test_id))
    chunk = CHUNK_SIZES[mode]
    per_row = 1 if chunk == 100 else 2
    markup = types.InlineKeyboardMarkup()
    row = []
    for s in range(0, total, chunk):
        e = min(s + chunk, total)
        row.append(types.InlineKeyboardButton(
            f"{s + 1}–{e}", callback_data=f"rng:{test_id}:{mode}:{s}"
        ))
        if len(row) == per_row:
            markup.row(*row)
            row = []
    if row:
        markup.row(*row)
    markup.add(types.InlineKeyboardButton("« Orqaga", callback_data=f"back:mode:{test_id}"))
    return markup


def kb_timer_menu(test_id, mode, start):
    markup = types.InlineKeyboardMarkup()
    btns = [
        types.InlineKeyboardButton(
            f"⏱ {t} soniya",
            callback_data=f"tmr:{test_id}:{mode}:{start}:{t}",
        )
        for t in TIMER_OPTIONS
    ]
    markup.row(*btns)
    if mode in CHUNK_SIZES:
        back_cb = f"back:rng:{test_id}:{mode}"
    else:
        back_cb = f"back:mode:{test_id}"
    markup.add(types.InlineKeyboardButton("« Orqaga", callback_data=back_cb))
    return markup


# ====== Sessiya boshqaruvi ======

def cancel_session_timer(state):
    t = state.get("timer_obj")
    if t:
        try:
            t.cancel()
        except Exception:
            pass
    state["timer_obj"] = None


def discard_session(chat_id):
    s = chat_session.pop(chat_id, None)
    if s:
        cancel_session_timer(s)
        pid = s.get("active_poll_id")
        if pid:
            poll_to_chat.pop(pid, None)
    return s


def format_q_text(state):
    quiz = state["quiz"]
    pos = state["pos"]
    q = quiz[pos]
    harflar = [chr(65 + i) for i in range(len(q["variantlar"]))]
    variants_text = "\n".join(f"<b>{harflar[i]})</b> {v}" for i, v in enumerate(q["variantlar"]))
    return f"<b>[{pos + 1}/{len(quiz)}]</b> {q['savol']}\n\n{variants_text}"


def _send_preview(chat_id, edit_msg_id, test_id, mode, start, timer, chat_type):
    quiz = build_session_quiz(test_id, mode, start)
    if not quiz:
        return False
    label = subset_label(mode, start, len(quiz))
    info = TESTS[test_id]
    total = len(get_quiz_template(test_id))
    is_solo = chat_type == "private"
    discard_session(chat_id)
    chat_session[chat_id] = {
        "test_id": test_id,
        "mode": mode,
        "start": start,
        "timer": timer,
        "quiz": quiz,
        "pos": 0,
        "label": label,
        "start_time": None,
        "user_scores": {},
        "active_poll_id": None,
        "active_poll_msg_id": None,
        "timer_obj": None,
        "solo": is_solo,
        "solo_advanced": False,
        "lock": threading.Lock(),
    }
    if is_solo:
        mode_line = "🧑 Yakka tartibda — javob berishingiz bilan keyingi savolga o'tadi"
    else:
        mode_line = "👥 Guruhda — hamma vaqt tugashini kutadi"
    text = (
        f"🎲 <b>\"{info['name']} [{total}]\"</b>\n\n"
        f"<i>{label}</i>\n\n"
        f"📝 <b>{len(quiz)} ta savol</b>\n"
        f"⏱ Har bir savol uchun <b>{timer} soniya</b>\n"
        f"🔁 Variantlar har testda aralashtiriladi\n"
        f"{mode_line}\n\n"
        f"▶️ Tayyor bo'lsangiz quyidagi tugmani bosing.\n"
        f"To'xtatish uchun /stop"
    )
    markup = _kb_preview(test_id, mode, start, timer, chat_type)
    if edit_msg_id is not None:
        try:
            bot.edit_message_text(
                chat_id=chat_id, message_id=edit_msg_id,
                text=text, reply_markup=markup, parse_mode="HTML",
            )
            return True
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")
    return True


def _kb_preview(test_id, mode, start, timer, chat_type):
    payload = f"{test_id}_{mode}_{start}_{timer}"
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("🚀 Bu testni boshlash", callback_data="go"))
    if chat_type == "private":
        username = bot_username()
        markup.add(types.InlineKeyboardButton(
            "👥 Guruhda testni boshlash",
            url=f"https://t.me/{username}?startgroup={payload}",
        ))
        share_url = f"https://t.me/{username}?start={payload}"
        markup.add(types.InlineKeyboardButton(
            "🔗 Testni ulashish",
            url=f"https://t.me/share/url?url={share_url}",
        ))
    return markup


def send_current_poll(chat_id):
    state = chat_session.get(chat_id)
    if not state:
        return
    cancel_session_timer(state)
    old_pid = state.get("active_poll_id")
    if old_pid:
        poll_to_chat.pop(old_pid, None)
        state["active_poll_id"] = None

    if state["pos"] >= len(state["quiz"]):
        finish_session(chat_id)
        return

    q = state["quiz"][state["pos"]]
    harflar = [chr(65 + i) for i in range(len(q["variantlar"]))]

    bot.send_message(chat_id, format_q_text(state), parse_mode="HTML")

    correct_text = q["variantlar"][q["javob_index"]]
    explanation = f"To'g'ri javob: {harflar[q['javob_index']]}) {correct_text}"
    if len(explanation) > 200:
        explanation = explanation[:197] + "..."

    poll_msg = bot.send_poll(
        chat_id=chat_id,
        question="Javobni tanlang:",
        options=harflar,
        type="quiz",
        correct_option_id=q["javob_index"],
        is_anonymous=False,
        open_period=state["timer"],
        explanation=explanation,
    )
    pid = poll_msg.poll.id
    state["active_poll_id"] = pid
    state["active_poll_msg_id"] = poll_msg.message_id
    state["solo_advanced"] = False
    poll_to_chat[pid] = chat_id

    t = threading.Timer(state["timer"] + REVEAL_DELAY, lambda: _advance(chat_id))
    t.daemon = True
    t.start()
    state["timer_obj"] = t


def _advance(chat_id):
    state = chat_session.get(chat_id)
    if not state:
        return
    with state["lock"]:
        state["pos"] += 1
    try:
        send_current_poll(chat_id)
    except Exception as e:
        print(f"_advance error: {e}")


def finish_session(chat_id):
    state = discard_session(chat_id)
    if not state:
        return
    info = TESTS[state["test_id"]]
    total = len(get_quiz_template(state["test_id"]))
    elapsed = time.time() - (state["start_time"] or time.time())
    n_quiz = len(state["quiz"])
    scores = state["user_scores"]

    if not scores:
        body = "Hech kim javob bermadi 🤷"
    else:
        ordered = sorted(
            scores.values(),
            key=lambda s: (-s["score"], s["wrong"], s["name"].lower()),
        )
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, s in enumerate(ordered):
            badge = medals[i] if i < len(medals) else f"<b>{i + 1}.</b>"
            answered = s["score"] + s["wrong"]
            skipped = max(0, (n_quiz - s["first_pos"]) - answered)
            lines.append(
                f"{badge} <b>{s['name']}</b>\n"
                f"   ✅ {s['score']}   ❌ {s['wrong']}   🦘 {skipped}"
            )
        body = "\n\n".join(lines)

    text = (
        f"🎯 <b>\"{info['name']} [{total}]\"</b> testi yakunlandi!\n\n"
        f"<i>{state['label']}</i>\n"
        f"⏱ Jami vaqt: {elapsed:.1f} soniya\n\n"
        f"{body}"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        "🔁 Qaytadan urinish",
        callback_data=f"rs:{state['test_id']}:{state['mode']}:{state['start']}:{state['timer']}",
    ))
    if state["mode"] in CHUNK_SIZES:
        back_cb = f"back:rng:{state['test_id']}:{state['mode']}"
    else:
        back_cb = f"back:mode:{state['test_id']}"
    markup.add(types.InlineKeyboardButton("« Orqaga", callback_data=back_cb))
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


# ====== Handler'lar ======

@bot.message_handler(commands=["start"])
def cmd_start(message):
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        payload = args[1].strip()
        parts = payload.split("_")
        if len(parts) == 4:
            test_id, mode, start_s, timer_s = parts
            if test_id in TESTS:
                try:
                    _send_preview(
                        chat_id=message.chat.id,
                        edit_msg_id=None,
                        test_id=test_id,
                        mode=mode,
                        start=int(start_s),
                        timer=int(timer_s),
                        chat_type=message.chat.type,
                    )
                    return
                except Exception as e:
                    print(f"deep link payload xato: {e}")
    discard_session(message.chat.id)
    bot.send_message(
        message.chat.id,
        "🧾 Iltimos, test mavzusini tanlang:",
        reply_markup=kb_main_menu(),
    )


@bot.message_handler(commands=["stop"])
def cmd_stop(message):
    if discard_session(message.chat.id):
        bot.send_message(message.chat.id, "⏹ Test to'xtatildi.\n/start orqali qaytadan boshlang.")
    else:
        bot.send_message(message.chat.id, "Hozir faol test yo'q.\n/start orqali boshlang.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("sel:"))
def cb_select_test(call):
    test_id = call.data.split(":")[1]
    info = TESTS.get(test_id)
    if not info:
        bot.answer_callback_query(call.id, "Test topilmadi.")
        return
    total = len(get_quiz_template(test_id))
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.id,
        text=f"📘 <b>{info['name']}</b> [{total}]\n\nTest turini tanlang 👇",
        reply_markup=kb_mode_menu(test_id),
        parse_mode="HTML",
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("mode:"))
def cb_select_mode(call):
    _, test_id, mode = call.data.split(":")
    info = TESTS.get(test_id)
    if not info:
        return
    if mode in ("full", "rand"):
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.id,
            text=f"📘 <b>{info['name']}</b>\n\nVaqt cheklovini tanlang 👇",
            reply_markup=kb_timer_menu(test_id, mode, 0),
            parse_mode="HTML",
        )
    else:
        total = len(get_quiz_template(test_id))
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.id,
            text=f"📘 <b>{info['name']}</b> [{total}]\n\nDiapazonni tanlang 👇",
            reply_markup=kb_range_menu(test_id, mode),
            parse_mode="HTML",
        )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("rng:"))
def cb_select_range(call):
    _, test_id, mode, start = call.data.split(":")
    info = TESTS.get(test_id)
    if not info:
        return
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.id,
        text=f"📘 <b>{info['name']}</b>\n\nVaqt cheklovini tanlang 👇",
        reply_markup=kb_timer_menu(test_id, mode, int(start)),
        parse_mode="HTML",
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("tmr:"))
def cb_select_timer(call):
    _, test_id, mode, start, timer = call.data.split(":")
    _send_preview(
        call.message.chat.id, call.message.id,
        test_id, mode, int(start), int(timer),
        call.message.chat.type,
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "go")
def cb_start_quiz(call):
    chat_id = call.message.chat.id
    state = chat_session.get(chat_id)
    if not state:
        bot.answer_callback_query(call.id, "Sessiya topilmadi. /start")
        return
    state["start_time"] = time.time()
    try:
        bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.id, reply_markup=None)
    except Exception:
        pass
    bot.answer_callback_query(call.id, "🚀 Boshladik!")
    send_current_poll(chat_id)


@bot.poll_answer_handler()
def on_poll_answer(poll_answer):
    pid = poll_answer.poll_id
    chat_id = poll_to_chat.get(pid)
    if chat_id is None:
        return
    state = chat_session.get(chat_id)
    if not state:
        return
    if not poll_answer.option_ids:
        return  # vote retracted
    user = poll_answer.user
    selected = poll_answer.option_ids[0]

    advance_after = False
    lock = state.get("lock")
    if lock is None:
        return
    with lock:
        if state.get("active_poll_id") != pid:
            return
        pos = state["pos"]
        if pos >= len(state["quiz"]):
            return
        q = state["quiz"][pos]
        correct = q["javob_index"]
        scores = state["user_scores"]
        if user.id not in scores:
            name = user.first_name or "Foydalanuvchi"
            if user.last_name:
                name += f" {user.last_name}"
            scores[user.id] = {
                "name": name,
                "score": 0,
                "wrong": 0,
                "first_pos": pos,
            }
        if selected == correct:
            scores[user.id]["score"] += 1
        else:
            scores[user.id]["wrong"] += 1

        if state.get("solo") and not state.get("solo_advanced"):
            state["solo_advanced"] = True
            advance_after = True

    # Solo (private) — javob bergan zahoti keyingi savolga o'tish
    if advance_after:
        cancel_session_timer(state)
        t = threading.Timer(REVEAL_DELAY, lambda: _advance(chat_id))
        t.daemon = True
        t.start()
        state["timer_obj"] = t


@bot.callback_query_handler(func=lambda c: c.data.startswith("rs:"))
def cb_restart(call):
    _, test_id, mode, start, timer = call.data.split(":")
    _send_preview(
        call.message.chat.id, None,
        test_id, mode, int(start), int(timer),
        call.message.chat.type,
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("back:"))
def cb_back(call):
    parts = call.data.split(":")
    where = parts[1]
    if where == "main":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.id,
            text="🧾 Iltimos, test mavzusini tanlang:",
            reply_markup=kb_main_menu(),
        )
    elif where == "mode":
        test_id = parts[2]
        info = TESTS.get(test_id)
        if not info:
            return
        total = len(get_quiz_template(test_id))
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.id,
            text=f"📘 <b>{info['name']}</b> [{total}]\n\nTest turini tanlang 👇",
            reply_markup=kb_mode_menu(test_id),
            parse_mode="HTML",
        )
    elif where == "rng":
        test_id, mode = parts[2], parts[3]
        info = TESTS.get(test_id)
        if not info:
            return
        total = len(get_quiz_template(test_id))
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.id,
            text=f"📘 <b>{info['name']}</b> [{total}]\n\nDiapazonni tanlang 👇",
            reply_markup=kb_range_menu(test_id, mode),
            parse_mode="HTML",
        )
    bot.answer_callback_query(call.id)


if __name__ == "__main__":
    bot.infinity_polling(allowed_updates=["message", "callback_query", "poll_answer"])
