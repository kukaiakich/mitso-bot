from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException, TimeoutException
from bs4 import BeautifulSoup as b
import telebot
from telebot import types
import re
import time
import json
import os
import threading
from datetime import datetime, timedelta

API_KEY = os.environ.get('TELEGRAM_BOT_TOKEN', '6091897495:AAGNE4b5SnIF_oEQCSFEwn42f2dmNwbOzOE')

URL = "https://apps.mitso.by/frontend/web/schedule/group-schedule"

if os.path.isdir('/data'):
    DATA_FILE = '/data/data.json'
else:
    DATA_FILE = 'data.json'


# ================== SELENIUM ==================

def wait_option(driver, select_id, text, timeout=20):
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located(
            (By.XPATH, f'//*[@id="{select_id}"]//option[normalize-space()="{text}"]')
        )
    )


def fetch_html_once():
    options = Options()

    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-setuid-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-software-rasterizer')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-logging')
    options.add_argument('--log-level=3')
    options.add_argument('--silent')
    options.add_argument('--no-default-browser-check')
    options.add_argument('--no-first-run')
    options.add_argument('--disable-background-networking')
    options.add_argument('--disable-sync')
    options.add_argument('--disable-translate')
    options.add_argument('--hide-scrollbars')
    options.add_argument('--mute-audio')
    options.add_argument('--renderer-process-limit=1')
    options.add_argument('--blink-settings=imagesEnabled=false')
    options.add_argument('--window-size=1280,800')
    options.add_argument(
        '--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    )
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
        "profile.default_content_setting_values.geolocation": 2,
    }
    options.add_experimental_option("prefs", prefs)

    driver = None
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(45)
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        driver.get(URL)

        wait_option(driver, "faculty-id", "Юридический")
        Select(driver.find_element(By.XPATH, '//*[@id="faculty-id"]')).select_by_visible_text("Юридический")

        wait_option(driver, "form-id", "Дневная")
        Select(driver.find_element(By.XPATH, '//*[@id="form-id"]')).select_by_visible_text("Дневная")

        wait_option(driver, "course-id", "3 курс")
        Select(driver.find_element(By.XPATH, '//*[@id="course-id"]')).select_by_visible_text("3 курс")

        wait_option(driver, "group-id", "2440 МП")
        Select(driver.find_element(By.XPATH, '//*[@id="group-id"]')).select_by_visible_text("2440 МП")

        wait_option(driver, "week-id", "Текущая неделя")
        Select(driver.find_element(By.XPATH, '//*[@id="week-id"]')).select_by_visible_text("Текущая неделя")

        btn = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, '//*[@id="w0"]/div[6]/div/button'))
        )
        btn.click()

        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located((By.XPATH, '//div[contains(@class,"table-responsive")]'))
        )
        time.sleep(1)

        return driver.page_source

    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def fetch_html(retries=3, delay=5):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            print(f'[parse] попытка {attempt}/{retries}', flush=True)
            html = fetch_html_once()
            if html and 'table-responsive' in html:
                print(f'[parse] успех на попытке {attempt}', flush=True)
                return html
            else:
                print(f'[parse] попытка {attempt}: пустой html', flush=True)
                last_err = 'пустой результат'
        except (TimeoutException, WebDriverException) as e:
            print(f'[parse] попытка {attempt} упала: {e}', flush=True)
            last_err = e
        except Exception as e:
            print(f'[parse] попытка {attempt} упала: {e}', flush=True)
            last_err = e

        if attempt < retries:
            time.sleep(delay)

    print(f'[parse] все {retries} попыток провалились: {last_err}', flush=True)
    return None


html = fetch_html(retries=3, delay=5)
if html is None:
    html = '<html></html>'


# ================== ПАРСИНГ ==================

RU_WEEKDAYS = [
    "Понедельник", "Вторник", "Среда",
    "Четверг", "Пятница", "Суббота", "Воскресенье"
]

RU_MONTHS = {
    'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
    'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
    'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
}

TEACHER_RE = re.compile(r'[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ]\.(?:\s*[А-ЯЁ]\.)?)')
IJA2_RE = re.compile(r'иностранн\w*\s+язык\s*2', re.IGNORECASE)
IJA1_RE = re.compile(r'иностранн\w*\s+язык(?!\s*2)', re.IGNORECASE)

soup = b(html, 'html.parser')


def weekday_from_title(title):
    t = title.lower()
    for wd in RU_WEEKDAYS:
        if wd.lower() in t:
            return wd
    return None


def date_from_title(title, default_year=None):
    m = re.search(r'(\d{1,2})\s+([а-яё]+)', title.lower())
    if not m:
        return None
    day = int(m.group(1))
    month = RU_MONTHS.get(m.group(2))
    if not month:
        return None
    year = default_year or datetime.now().year
    return datetime(year, month, day).date()


def get_raw_schedule(weekday=None, target_date=None):
    result = []

    for h2 in soup.find_all('h2'):
        title = h2.get_text(" ", strip=True)

        if weekday and weekday_from_title(title) != weekday:
            continue
        if target_date and date_from_title(title) != target_date:
            continue

        container = h2.find_next_sibling('div', class_='table-responsive') \
                    or h2.find_next('div', class_='table-responsive')
        if not container:
            continue

        rows = []
        for tr in container.find_all('tr'):
            cells = tr.find_all(['td', 'th'])
            texts = [c.get_text(" ", strip=True) for c in cells]
            texts = [t for t in texts if t]

            if not texts:
                continue
            if texts[0].lower().startswith('время'):
                continue

            time_ = texts[0] if len(texts) > 0 else ''
            subject = texts[1] if len(texts) > 1 else ''
            room = texts[2] if len(texts) > 2 else ''

            if not subject:
                continue

            rows.append({'time': time_, 'subject': subject, 'room': room})

        if rows:
            result.append({'title': title, 'rows': rows})

        if weekday:
            break

    return result


def extract_teachers(pattern):
    teachers = set()
    for block in get_raw_schedule():
        for r in block['rows']:
            if pattern.search(r['subject']):
                for m in TEACHER_RE.finditer(r['subject']):
                    teachers.add(m.group(0).strip())
    return sorted(teachers)


def filter_subject_in_rows(rows, pattern, teacher):
    if not teacher:
        return rows

    last_name = teacher.split()[0]
    out = []

    for r in rows:
        if pattern.search(r['subject']):
            names = TEACHER_RE.findall(r['subject'])
            if names:
                matching = [n for n in names if n.startswith(last_name)]
                if not matching:
                    continue
                new_subject = r['subject']
                for n in names:
                    if n not in matching:
                        new_subject = new_subject.replace(n, '')
                new_subject = re.sub(r'(\s*,\s*)+', ', ', new_subject)
                new_subject = re.sub(r'\s+', ' ', new_subject).strip()
                new_subject = new_subject.rstrip(', ').strip()
                r = dict(r)
                r['subject'] = new_subject
        out.append(r)

    return out


def format_time(t):
    return t.replace('.', ':').replace('-', '–').strip()


def format_day(title, rows):
    lessons = []
    for r in rows:
        subject = r['subject'].strip()
        if not subject or 'нет занятий' in subject.lower():
            continue

        block = [f'📚 {subject}']
        if r['time']:
            block.append(f'🕐 {format_time(r["time"])}')
        room = r['room'].strip()
        if room and room.lower() != 'нет':
            block.append(f'📍 ауд. {room}')
        lessons.append('\n'.join(block))

    if not lessons:
        return None

    return f'📅 {title}\n\n' + '\n\n'.join(lessons)


# ================== БОТ ==================

bot = telebot.TeleBot(API_KEY)

user_teachers = {}
broadcasts = {}
last_sent = {}


def save_data():
    data = {
        'user_teachers': {str(k): v for k, v in user_teachers.items()},
        'broadcasts': {str(k): v for k, v in broadcasts.items()},
        'last_sent': {str(k): v for k, v in last_sent.items()},
    }
    tmp = DATA_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
    except Exception as e:
        print('save error:', e, flush=True)


def load_data():
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print('load error:', e, flush=True)
        return

    for k, v in data.get('user_teachers', {}).items():
        user_teachers[int(k)] = v
    for k, v in data.get('broadcasts', {}).items():
        broadcasts[int(k)] = v
    for k, v in data.get('last_sent', {}).items():
        last_sent[int(k)] = v


load_data()


def display_schedule(chat_id, weekday=None):
    blocks = get_raw_schedule(weekday=weekday)
    sel = user_teachers.get(chat_id, {})

    if not blocks:
        return f'На {weekday} пар нет.' if weekday else 'Расписание не найдено.'

    out = []
    for block in blocks:
        rows = block['rows']
        if sel.get('ija1'):
            rows = filter_subject_in_rows(rows, IJA1_RE, sel['ija1'])
        if sel.get('ija2'):
            rows = filter_subject_in_rows(rows, IJA2_RE, sel['ija2'])

        text = format_day(block['title'], rows)
        if text:
            out.append(text)

    if not out:
        return 'Пар нет.'
    return '\n\n'.join(out)


def days_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add('Понедельник', 'Вторник', 'Среда')
    kb.add('Четверг', 'Пятница', 'Суббота')
    kb.add('👤 Преподаватель ИЯ', '👤 Преподаватель ИЯ2')
    kb.add('🔔 Рассылка', '♻️ Сброс')
    return kb


@bot.message_handler(commands=['start', 'начать'])
def cmd_start(message):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton('🚀 Старт', callback_data='start'))
    bot.send_message(
        message.chat.id,
        f'Привет, {message.from_user.first_name}! 👋\n\n'
        'Я бот расписания группы 2440 МП.\n'
        'Нажми «Старт», чтобы выбрать день.',
        reply_markup=kb
    )


@bot.callback_query_handler(func=lambda call: call.data == 'start')
def cb_start(call):
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        'Выбери день недели 👇',
        reply_markup=days_keyboard()
    )


@bot.message_handler(func=lambda m: m.text in RU_WEEKDAYS)
def btn_day(message):
    bot.send_message(
        message.chat.id,
        display_schedule(message.chat.id, weekday=message.text),
        reply_markup=days_keyboard()
    )


@bot.message_handler(func=lambda m: m.text == '👤 Преподаватель ИЯ')
def btn_teacher_ija1(message):
    show_teachers(message, 'ija1', IJA1_RE, 'Иностранному языку')


@bot.message_handler(func=lambda m: m.text == '👤 Преподаватель ИЯ2')
def btn_teacher_ija2(message):
    show_teachers(message, 'ija2', IJA2_RE, 'Иностранному языку 2')


def show_teachers(message, subject_key, pattern, subject_label):
    teachers = extract_teachers(pattern)
    if not teachers:
        bot.send_message(message.chat.id,
                         f'Преподаватели по {subject_label} не найдены.',
                         reply_markup=days_keyboard())
        return

    kb = types.InlineKeyboardMarkup(row_width=2)
    for t in teachers:
        kb.add(types.InlineKeyboardButton(t, callback_data=f'{subject_key}_{t}'))

    current = user_teachers.get(message.chat.id, {}).get(subject_key)
    text = f'Выбери своего преподавателя по {subject_label}:'
    if current:
        text += f'\n\nСейчас выбран: {current}'

    bot.send_message(message.chat.id, text, reply_markup=kb)


@bot.callback_query_handler(func=lambda call: call.data.startswith('ija1_') or call.data.startswith('ija2_'))
def cb_teacher(call):
    if call.data.startswith('ija1_'):
        subject_key, label = 'ija1', 'ИЯ'
        teacher = call.data[len('ija1_'):]
    else:
        subject_key, label = 'ija2', 'ИЯ2'
        teacher = call.data[len('ija2_'):]

    user_teachers.setdefault(call.message.chat.id, {})[subject_key] = teacher
    save_data()
    bot.answer_callback_query(call.id, f'{label}: {teacher}')
    bot.send_message(
        call.message.chat.id,
        f'✅ Преподаватель {label}: {teacher}\n\n'
        f'Теперь в расписании по этому предмету будет показан только он.',
        reply_markup=days_keyboard()
    )


@bot.message_handler(func=lambda m: m.text == '🔔 Рассылка')
def btn_broadcast(message):
    chat_id = message.chat.id
    current = broadcasts.get(chat_id)

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton('⏰ Установить время', callback_data='bc_set'))
    if current:
        kb.add(types.InlineKeyboardButton('❌ Отменить рассылку', callback_data='bc_cancel'))

    text = '🔔 Ежедневная рассылка расписания'
    if current:
        text += f'\n\nСейчас установлено: {current}'
    else:
        text += '\n\nПока не настроено.'
    text += '\n\nБот будет присылать расписание на текущий день в выбранное время.'

    bot.send_message(chat_id, text, reply_markup=kb)


@bot.callback_query_handler(func=lambda call: call.data == 'bc_set')
def cb_bc_set(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        'Во сколько присылать расписание?\n\n'
        'Формат: ЧЧ:ММ, например 07:30'
    )
    bot.register_next_step_handler(msg, save_broadcast_time)


def save_broadcast_time(message):
    text = (message.text or '').strip()
    m = re.match(r'^(\d{1,2}):(\d{2})$', text)
    if not m:
        bot.send_message(message.chat.id, 'Не понял время. Формат: 07:30',
                         reply_markup=days_keyboard())
        return

    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h < 24 and 0 <= mi < 60):
        bot.send_message(message.chat.id, 'Некорректное время. Часы 0–23, минуты 0–59.',
                         reply_markup=days_keyboard())
        return

    time_str = f'{h:02d}:{mi:02d}'
    broadcasts[message.chat.id] = time_str
    save_data()
    bot.send_message(
        message.chat.id,
        f'✅ Рассылка установлена на {time_str}.\n'
        f'Каждый день в это время будет приходить расписание.',
        reply_markup=days_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data == 'bc_cancel')
def cb_bc_cancel(call):
    broadcasts.pop(call.message.chat.id, None)
    save_data()
    bot.answer_callback_query(call.id, 'Отменено')
    bot.send_message(call.message.chat.id, '❌ Рассылка отменена.',
                     reply_markup=days_keyboard())


@bot.message_handler(func=lambda m: m.text == '♻️ Сброс')
def btn_reset(message):
    chat_id = message.chat.id
    user_teachers.pop(chat_id, None)
    broadcasts.pop(chat_id, None)
    last_sent.pop(chat_id, None)
    save_data()
    bot.send_message(
        chat_id,
        '♻️ Все твои настройки сброшены:\n'
        '• преподаватели ИЯ/ИЯ2 — очищены\n'
        '• рассылка — отключена',
        reply_markup=days_keyboard()
    )


def broadcast_loop():
    while True:
        try:
            now = datetime.now()
            hm = now.strftime('%H:%M')
            day_key = now.strftime('%Y-%m-%d') + ' ' + hm

            for chat_id, t in list(broadcasts.items()):
                if t == hm and last_sent.get(chat_id) != day_key:
                    last_sent[chat_id] = day_key
                    save_data()
                    today = now.date()
                    wd = RU_WEEKDAYS[today.weekday()]
                    text = display_schedule(chat_id, weekday=wd)
                    try:
                        bot.send_message(
                            chat_id,
                            f'🔔 Расписание на сегодня ({today.strftime("%d.%m")}):\n\n{text}',
                            reply_markup=days_keyboard()
                        )
                    except Exception as e:
                        print('send error:', e, flush=True)
        except Exception as e:
            print('broadcast loop error:', e, flush=True)

        time.sleep(30)


threading.Thread(target=broadcast_loop, daemon=True).start()


bot.polling(none_stop=True, interval=1, timeout=30)