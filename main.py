import json
import os
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import truststore
truststore.inject_into_ssl()

import certifi
import requests
import telebot
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from telebot import types
from urllib3.util.retry import Retry


 # ================== НАСТРОЙКИ ==================
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "Не задана переменная окружения TELEGRAM_BOT_TOKEN"
    )

URL = "https://apps.mitso.by/frontend/web/schedule/group-schedule"

if os.path.isdir("/data"):
    DATA_FILE = "/data/data.json"
else:
    DATA_FILE = "data.json"


TZ = ZoneInfo("Europe/Minsk")

# ================== ПОЛУЧЕНИЕ HTML ЧЕРЕЗ REQUESTS ==================

# ================== ПОЛУЧЕНИЕ HTML ЧЕРЕЗ REQUESTS ==================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}

def create_session():
    session = requests.Session()

    if os.name != "nt":
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        session.verify = False

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=5,
        pool_maxsize=5,
    )

    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(HEADERS)

    return session




def normalize_text(value):
    return " ".join(value.split()).strip().casefold()


def get_option_value(select, visible_text):
    target = normalize_text(visible_text)

    for option in select.find_all("option"):
        option_text = normalize_text(option.get_text(" ", strip=True))

        if option_text == target:
            return option.get("value", "")

    available = [
        option.get_text(" ", strip=True)
        for option in select.find_all("option")
    ]

    raise RuntimeError(
        f'В поле "{select.get("id")}" не найден вариант '
        f'"{visible_text}". Доступные варианты: {available}'
    )


def fetch_html_once():
    session = create_session()

    response = session.get(URL, timeout=45)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"

    soup = BeautifulSoup(response.text, "html.parser")
    form = soup.find("form")

    if not form:
        raise RuntimeError("На странице не найдена форма расписания")

    action = form.get("action") or URL
    method = (form.get("method") or "get").lower()

    action = requests.compat.urljoin(URL, action)

    # Скрытые поля формы, включая возможный CSRF-токен.
    payload = {}

    for input_tag in form.find_all("input"):
        name = input_tag.get("name")
        input_type = (input_tag.get("type") or "text").lower()

        if not name:
            continue

        if input_type in {"hidden", "submit"}:
            payload[name] = input_tag.get("value", "")

    def load_dependent_options(endpoint, parents):
        ajax_url = requests.compat.urljoin(
            URL.rsplit("/", 1)[0] + "/",
            endpoint,
        )
        ajax_payload = [
            ("depdrop_parents[]", value)
            for value in parents
        ]

        csrf_token = payload.get("_csrf-frontend")
        if csrf_token:
            ajax_payload.append(("_csrf-frontend", csrf_token))
        ajax_response = session.post(
            ajax_url,
            data=ajax_payload,
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=45,
        )
        ajax_response.raise_for_status()

        try:
            data = ajax_response.json()
        except ValueError as error:
            raise RuntimeError(
                f"Сервер вернул неверный ответ для {endpoint}"
            ) from error

        options = data.get("output", [])
        if not options:
            raise RuntimeError(
                f"Сайт не вернул варианты для поля {endpoint}"
            )

        return options

    faculty_select = form.find("select", id="faculty-id")
    if not faculty_select:
        raise RuntimeError('На странице не найден select с id="faculty-id"')

    faculty_name = faculty_select.get("name")
    faculty_id = get_option_value(faculty_select, "Юридический")
    payload[faculty_name] = faculty_id

    form_options = load_dependent_options("education", [faculty_id])
    form_option = next(
        (
            option for option in form_options
            if normalize_text(option.get("name", "")) == normalize_text("Дневная")
        ),
        None,
    )
    if not form_option:
        raise RuntimeError(
            "На сайте не найдена форма обучения «Дневная»"
        )
    form_id = form_option["id"]

    course_options = load_dependent_options(
        "course", [faculty_id, form_id]
    )
    course_option = next(
        (
            option for option in course_options
            if normalize_text(option.get("name", "")) == normalize_text("3 курс")
        ),
        None,
    )
    if not course_option:
        raise RuntimeError("На сайте не найден 3 курс")
    course_id = course_option["id"]

    group_options = load_dependent_options(
        "group", [faculty_id, form_id, course_id]
    )
    group_option = next(
        (
            option for option in group_options
            if re.sub(
                r"[^a-zа-яё0-9]",
                "",
                normalize_text(option.get("name", "")),
            ).replace("мп", "mp") == "2440mp"
        ),
        None,
    )
    if not group_option:
        raise RuntimeError("На сайте не найдена группа «2440 МП»")
    group_id = group_option["id"]

    week_options = load_dependent_options(
        "week", [faculty_id, form_id, course_id, group_id]
    )
    week_option = next(
        (
            option for option in week_options
            if normalize_text(option.get("name", ""))
            == normalize_text("Текущая неделя")
        ),
        week_options[0],
    )

    select_values = {
        "form-id": form_id,
        "course-id": course_id,
        "group-id": group_id,
        "week-id": week_option["id"],
    }
    for select_id, value in select_values.items():
        select = form.find("select", id=select_id)
        if not select or not select.get("name"):
            raise RuntimeError(
                f'У select "{select_id}" отсутствует атрибут name'
            )
        payload[select["name"]] = value

    # Отправляем форму.
    if method == "post":
        result = session.post(
            action,
            data=payload,
            timeout=45,
        )
    else:
        result = session.get(
            action,
            params=payload,
            timeout=45,
        )

    result.raise_for_status()
    result.encoding = result.apparent_encoding or "utf-8"

    if "table-responsive" not in result.text:
        raise RuntimeError(
            "Сервер вернул страницу без расписания. "
            f"Фактический URL: {result.url}"
        )

    return result.text


def fetch_html(retries=3, delay=5):
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            print(
                f"[parse] попытка {attempt}/{retries}",
                flush=True,
            )

            html = fetch_html_once()

            if html and "table-responsive" in html:
                print(
                    f"[parse] успешно на попытке {attempt}",
                    flush=True,
                )
                return html

            last_error = "HTML не содержит расписание"

        except requests.RequestException as error:
            last_error = error
            print(
                f"[parse] ошибка HTTP: {error}",
                flush=True,
            )

        except Exception as error:
            last_error = error
            print(
                f"[parse] ошибка: {error}",
                flush=True,
            )

        if attempt < retries:
            time.sleep(delay)

    print(
        f"[parse] все попытки завершились ошибкой: {last_error}",
        flush=True,
    )

    return None


html = fetch_html()

if html is None:
    html = "<html></html>"


# ================== ПАРСИНГ ==================

RU_WEEKDAYS = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]

RU_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

TEACHER_RE = re.compile(
    r"[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ]\.(?:\s*[А-ЯЁ]\.)?)"
)

IJA2_RE = re.compile(
    r"иностранн\w*\s+язык\s*2",
    re.IGNORECASE,
)

IJA1_RE = re.compile(
    r"иностранн\w*\s+язык(?!\s*2)",
    re.IGNORECASE,
)

soup = BeautifulSoup(html, "html.parser")


def weekday_from_title(title):
    title_lower = title.lower()

    for weekday in RU_WEEKDAYS:
        if weekday.lower() in title_lower:
            return weekday

    return None


def date_from_title(title, default_year=None):
    match = re.search(
        r"(\d{1,2})\s+([а-яё]+)",
        title.lower(),
    )

    if not match:
        return None

    day = int(match.group(1))
    month = RU_MONTHS.get(match.group(2))

    if not month:
        return None

    year = default_year or datetime.now(TZ).year

    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def get_raw_schedule(weekday=None, target_date=None):
    result = []

    for h2 in soup.find_all("h2"):
        title = h2.get_text(" ", strip=True)

        if weekday and weekday_from_title(title) != weekday:
            continue

        if target_date and date_from_title(title) != target_date:
            continue

        container = (
            h2.find_next_sibling(
                "div",
                class_="table-responsive",
            )
            or h2.find_next(
                "div",
                class_="table-responsive",
            )
        )

        if not container:
            continue

        rows = []

        for tr in container.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            texts = [
                cell.get_text(" ", strip=True)
                for cell in cells
            ]
            texts = [text for text in texts if text]

            if not texts:
                continue

            if texts[0].lower().startswith("время"):
                continue

            time_value = texts[0] if len(texts) > 0 else ""
            subject = texts[1] if len(texts) > 1 else ""
            room = texts[2] if len(texts) > 2 else ""

            if not subject:
                continue

            rows.append(
                {
                    "time": time_value,
                    "subject": subject,
                    "room": room,
                }
            )

        if rows:
            result.append(
                {
                    "title": title,
                    "rows": rows,
                }
            )

        if weekday:
            break

    return result


def extract_teachers(pattern):
    teachers = set()

    for block in get_raw_schedule():
        for row in block["rows"]:
            if pattern.search(row["subject"]):
                for match in TEACHER_RE.finditer(row["subject"]):
                    teachers.add(match.group(0).strip())

    return sorted(teachers)


def filter_subject_in_rows(rows, pattern, teacher):
    if not teacher:
        return rows

    last_name = teacher.split()[0]
    result = []

    for row in rows:
        if pattern.search(row["subject"]):
            names = TEACHER_RE.findall(row["subject"])

            if names:
                matching = [
                    name
                    for name in names
                    if name.startswith(last_name)
                ]

                if not matching:
                    continue

                new_subject = row["subject"]

                for name in names:
                    if name not in matching:
                        new_subject = new_subject.replace(name, "")

                new_subject = re.sub(
                    r"(\s*,\s*)+",
                    ", ",
                    new_subject,
                )
                new_subject = re.sub(
                    r"\s+",
                    " ",
                    new_subject,
                ).strip()
                new_subject = new_subject.rstrip(", ").strip()

                row = dict(row)
                row["subject"] = new_subject

        result.append(row)

    return result


def format_time(value):
    return (
        value
        .replace(".", ":")
        .replace("-", "–")
        .strip()
    )


def format_day(title, rows):
    lessons = []

    for row in rows:
        subject = row["subject"].strip()

        if not subject:
            continue

        if "нет занятий" in subject.lower():
            continue

        block = [f"📚 {subject}"]

        if row["time"]:
            block.append(
                f'🕐 {format_time(row["time"])}'
            )

        room = row["room"].strip()

        if room and room.lower() != "нет":
            block.append(f"📍 ауд. {room}")

        lessons.append("\n".join(block))

    if not lessons:
        return None

    return f"📅 {title}\n\n" + "\n\n".join(lessons)


# ================== TELEGRAM-БОТ ==================

bot = telebot.TeleBot(TELEGRAM_TOKEN)

user_teachers = {}
broadcasts = {}
last_sent = {}


def save_data():
    data = {
        "user_teachers": {
            str(key): value
            for key, value in user_teachers.items()
        },
        "broadcasts": {
            str(key): value
            for key, value in broadcasts.items()
        },
        "last_sent": {
            str(key): value
            for key, value in last_sent.items()
        },
    }

    temp_file = DATA_FILE + ".tmp"

    try:
        with open(temp_file, "w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(temp_file, DATA_FILE)

    except Exception as error:
        print(f"Ошибка сохранения данных: {error}", flush=True)


def load_data():
    if not os.path.exists(DATA_FILE):
        return

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

    except Exception as error:
        print(f"Ошибка загрузки данных: {error}", flush=True)
        return

    for key, value in data.get("user_teachers", {}).items():
        user_teachers[int(key)] = value

    for key, value in data.get("broadcasts", {}).items():
        broadcasts[int(key)] = value

    for key, value in data.get("last_sent", {}).items():
        last_sent[int(key)] = value


load_data()


def display_schedule(chat_id, weekday=None):
    blocks = get_raw_schedule(weekday=weekday)
    selected = user_teachers.get(chat_id, {})

    if not blocks:
        if weekday:
            return f"На {weekday} пар нет."

        return "Расписание не найдено."

    result = []

    for block in blocks:
        rows = block["rows"]

        if selected.get("ija1"):
            rows = filter_subject_in_rows(
                rows,
                IJA1_RE,
                selected["ija1"],
            )

        if selected.get("ija2"):
            rows = filter_subject_in_rows(
                rows,
                IJA2_RE,
                selected["ija2"],
            )

        text = format_day(block["title"], rows)

        if text:
            result.append(text)

    if not result:
        return "Пар нет."

    return "\n\n".join(result)


def days_keyboard():
    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=3,
    )

    keyboard.add(
        "Понедельник",
        "Вторник",
        "Среда",
    )
    keyboard.add(
        "Четверг",
        "Пятница",
        "Суббота",
    )
    keyboard.add(
        "👤 Преподаватель ИЯ",
        "👤 Преподаватель ИЯ2",
    )
    keyboard.add(
        "🔔 Рассылка",
        "♻️ Сброс",
    )

    return keyboard


@bot.message_handler(commands=["start", "начать"])
def command_start(message):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(
            "🚀 Старт",
            callback_data="start",
        )
    )

    bot.send_message(
        message.chat.id,
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        "Я бот расписания группы 2440 МП.\n"
        "Нажми «Старт», чтобы выбрать день.",
        reply_markup=keyboard,
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "start"
)
def callback_start(call):
    bot.answer_callback_query(call.id)

    bot.send_message(
        call.message.chat.id,
        "Выбери день недели 👇",
        reply_markup=days_keyboard(),
    )


@bot.message_handler(
    func=lambda message: message.text in RU_WEEKDAYS
)
def button_day(message):
    bot.send_message(
        message.chat.id,
        display_schedule(
            message.chat.id,
            weekday=message.text,
        ),
        reply_markup=days_keyboard(),
    )


@bot.message_handler(
    func=lambda message: (
        message.text == "👤 Преподаватель ИЯ"
    )
)
def button_teacher_ija1(message):
    show_teachers(
        message,
        "ija1",
        IJA1_RE,
        "Иностранному языку",
    )


@bot.message_handler(
    func=lambda message: (
        message.text == "👤 Преподаватель ИЯ2"
    )
)
def button_teacher_ija2(message):
    show_teachers(
        message,
        "ija2",
        IJA2_RE,
        "Иностранному языку 2",
    )


def show_teachers(
    message,
    subject_key,
    pattern,
    subject_label,
):
    teachers = extract_teachers(pattern)

    if not teachers:
        bot.send_message(
            message.chat.id,
            f"Преподаватели по {subject_label} не найдены.",
            reply_markup=days_keyboard(),
        )
        return

    keyboard = types.InlineKeyboardMarkup(row_width=2)

    for teacher in teachers:
        keyboard.add(
            types.InlineKeyboardButton(
                teacher,
                callback_data=f"{subject_key}_{teacher}",
            )
        )

    current = user_teachers.get(
        message.chat.id,
        {},
    ).get(subject_key)

    text = (
        f"Выбери своего преподавателя "
        f"по {subject_label}:"
    )

    if current:
        text += f"\n\nСейчас выбран: {current}"

    bot.send_message(
        message.chat.id,
        text,
        reply_markup=keyboard,
    )


@bot.callback_query_handler(
    func=lambda call: (
        call.data.startswith("ija1_")
        or call.data.startswith("ija2_")
    )
)
def callback_teacher(call):
    if call.data.startswith("ija1_"):
        subject_key = "ija1"
        label = "ИЯ"
        teacher = call.data[len("ija1_"):]
    else:
        subject_key = "ija2"
        label = "ИЯ2"
        teacher = call.data[len("ija2_"):]

    user_teachers.setdefault(
        call.message.chat.id,
        {},
    )[subject_key] = teacher

    save_data()

    bot.answer_callback_query(
        call.id,
        f"{label}: {teacher}",
    )

    bot.send_message(
        call.message.chat.id,
        f"✅ Преподаватель {label}: {teacher}\n\n"
        "Теперь в расписании по этому предмету "
        "будет показан только он.",
        reply_markup=days_keyboard(),
    )


@bot.message_handler(
    func=lambda message: message.text == "🔔 Рассылка"
)
def button_broadcast(message):
    chat_id = message.chat.id
    current = broadcasts.get(chat_id)

    keyboard = types.InlineKeyboardMarkup(row_width=1)

    keyboard.add(
        types.InlineKeyboardButton(
            "⏰ Установить время",
            callback_data="bc_set",
        )
    )

    if current:
        keyboard.add(
            types.InlineKeyboardButton(
                "❌ Отменить рассылку",
                callback_data="bc_cancel",
            )
        )

    text = "🔔 Ежедневная рассылка расписания"

    if current:
        text += f"\n\nСейчас установлено: {current}"
    else:
        text += "\n\nПока не настроено."

    text += (
        "\n\nБот будет присылать расписание "
        "на текущий день в выбранное время."
    )

    bot.send_message(
        chat_id,
        text,
        reply_markup=keyboard,
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "bc_set"
)
def callback_broadcast_set(call):
    bot.answer_callback_query(call.id)

    message = bot.send_message(
        call.message.chat.id,
        "Во сколько присылать расписание?\n\n"
        "Формат: ЧЧ:ММ, например 07:30",
    )

    bot.register_next_step_handler(
        message,
        save_broadcast_time,
    )


def save_broadcast_time(message):
    text = (message.text or "").strip()

    match = re.match(
        r"^(\d{1,2}):(\d{2})$",
        text,
    )

    if not match:
        bot.send_message(
            message.chat.id,
            "Не понял время. Формат: 07:30",
            reply_markup=days_keyboard(),
        )
        return

    hours = int(match.group(1))
    minutes = int(match.group(2))

    if not (0 <= hours < 24 and 0 <= minutes < 60):
        bot.send_message(
            message.chat.id,
            "Некорректное время. Часы 0–23, "
            "минуты 0–59.",
            reply_markup=days_keyboard(),
        )
        return

    time_string = f"{hours:02d}:{minutes:02d}"

    broadcasts[message.chat.id] = time_string
    save_data()

    bot.send_message(
        message.chat.id,
        f"✅ Рассылка установлена на {time_string}.\n"
        "Каждый день в это время будет приходить "
        "расписание.",
        reply_markup=days_keyboard(),
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "bc_cancel"
)
def callback_broadcast_cancel(call):
    broadcasts.pop(call.message.chat.id, None)
    save_data()

    bot.answer_callback_query(call.id, "Отменено")

    bot.send_message(
        call.message.chat.id,
        "❌ Рассылка отменена.",
        reply_markup=days_keyboard(),
    )


@bot.message_handler(
    func=lambda message: message.text == "♻️ Сброс"
)
def button_reset(message):
    chat_id = message.chat.id

    user_teachers.pop(chat_id, None)
    broadcasts.pop(chat_id, None)
    last_sent.pop(chat_id, None)

    save_data()

    bot.send_message(
        chat_id,
        "♻️ Все твои настройки сброшены:\n"
        "• преподаватели ИЯ/ИЯ2 — очищены\n"
        "• рассылка — отключена",
        reply_markup=days_keyboard(),
    )


def broadcast_loop():
    while True:
        try:
            now = datetime.now(TZ)
            current_time = now.strftime("%H:%M")
            day_key = (
                now.strftime("%Y-%m-%d")
                + " "
                + current_time
            )

            for chat_id, configured_time in list(
                broadcasts.items()
            ):
                if (
                    configured_time == current_time
                    and last_sent.get(chat_id) != day_key
                ):
                    last_sent[chat_id] = day_key
                    save_data()

                    weekday = RU_WEEKDAYS[now.weekday()]
                    schedule = display_schedule(
                        chat_id,
                        weekday=weekday,
                    )

                    try:
                        bot.send_message(
                            chat_id,
                            (
                                "🔔 Расписание на сегодня "
                                f"({now.strftime('%d.%m')}):\n\n"
                                f"{schedule}"
                            ),
                            reply_markup=days_keyboard(),
                        )

                    except Exception as error:
                        print(
                            f"Ошибка отправки рассылки: {error}",
                            flush=True,
                        )

        except Exception as error:
            print(
                f"Ошибка цикла рассылки: {error}",
                flush=True,
            )

        time.sleep(30)


threading.Thread(
    target=broadcast_loop,
    daemon=True,
).start()


print("Бот запущен", flush=True)

bot.infinity_polling(
    timeout=30,
    long_polling_timeout=30,
)

print (date_from_title)