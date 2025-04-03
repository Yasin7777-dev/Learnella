import os
import logging
import aiohttp
import tempfile
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env

# Initialize logging
logging.basicConfig(level=logging.INFO)

# Bot and API configuration
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_BASE_URL = os.getenv("API_BASE_URL")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Session storage for user tokens and session data
USER_SESSIONS = {}

# ----- State Management Classes -----

# Authentication states
class AuthStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_password = State()

# Teacher states
class TeacherStates(StatesGroup):
    waiting_for_audio = State()
    waiting_for_title = State()
    waiting_for_description = State()
    waiting_for_subject = State()
    waiting_for_content_type = State()
    waiting_for_count = State()

# Student states
class StudentStates(StatesGroup):
    waiting_for_subject = State()
    waiting_for_term = State()
    in_learning_session = State()
    in_review_session = State()

# ----- Handlers -----

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = ["👨‍🏫 Login as Teacher", "👨‍🎓 Login as Student"]
    keyboard.add(*buttons)
    await message.answer("Welcome to Attendo Learning Bot! Please login to continue.", reply_markup=keyboard)

@dp.message_handler(lambda message: message.text in ["👨‍🏫 Login as Teacher", "👨‍🎓 Login as Student"])
async def login_handler(message: types.Message):
    user_id = message.from_user.id
    USER_SESSIONS[user_id] = {"role": "teacher" if "Teacher" in message.text else "student"}
    await AuthStates.waiting_for_username.set()
    await message.answer("Please enter your Attendo username:")

@dp.message_handler(state=AuthStates.waiting_for_username)
async def process_username(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    USER_SESSIONS[user_id]["username"] = message.text
    await AuthStates.waiting_for_password.set()
    await message.answer("Please enter your password:")

@dp.message_handler(state=AuthStates.waiting_for_password)
async def process_password(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    username = USER_SESSIONS[user_id]["username"]
    password = message.text
    role = USER_SESSIONS[user_id]["role"]
    
    # Delete the message with password for security
    await bot.delete_message(message.chat.id, message.message_id)
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{API_BASE_URL}/api/token/",
            json={"username": username, "password": password}
        ) as response:
            if response.status == 200:
                data = await response.json()
                USER_SESSIONS[user_id]["access_token"] = data["access"]
                USER_SESSIONS[user_id]["refresh_token"] = data["refresh"]
                await state.finish()
                if role == "teacher":
                    await show_teacher_menu(message)
                else:
                    await show_student_menu(message)
            else:
                await message.answer("❌ Login failed. Please try again.")
                await AuthStates.waiting_for_username.set()
                await message.answer("Please enter your Attendo username:")

# ----- Teacher Functionality -----

async def show_teacher_menu(message: types.Message):
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = [
        "📝 Upload Audio for Content Generation",
        "📊 View Generated Content",
        "📚 View Student Progress",
        "📋 Help"
    ]
    keyboard.add(*buttons)
    await message.answer("Teacher Dashboard - What would you like to do today?", reply_markup=keyboard)

@dp.message_handler(lambda message: message.text == "📝 Upload Audio for Content Generation")
async def upload_audio_handler(message: types.Message):
    user_id = message.from_user.id
    if user_id not in USER_SESSIONS or "access_token" not in USER_SESSIONS[user_id]:
        await message.answer("Please login first.")
        return
    
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {USER_SESSIONS[user_id]['access_token']}"}
        async with session.get(f"{API_BASE_URL}/api/core/subjects/", headers=headers) as response:
            if response.status == 200:
                subjects = await response.json()
                keyboard = types.InlineKeyboardMarkup()
                for subject in subjects:
                    keyboard.add(types.InlineKeyboardButton(
                        text=subject["name"],
                        callback_data=f"subject_{subject['id']}"
                    ))
                await TeacherStates.waiting_for_subject.set()
                await message.answer("Please select a subject:", reply_markup=keyboard)
            else:
                await message.answer("❌ Failed to load subjects. Please try again later.")

@dp.callback_query_handler(lambda c: c.data.startswith("subject_"), state=TeacherStates.waiting_for_subject)
async def process_subject_selection(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    subject_id = callback_query.data.split("_")[1]
    async with state.proxy() as data:
        data["subject_id"] = subject_id
    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(callback_query.from_user.id, "Please send an audio recording of your lesson.")
    await TeacherStates.waiting_for_audio.set()

@dp.message_handler(content_types=types.ContentType.VOICE, state=TeacherStates.waiting_for_audio)
@dp.message_handler(content_types=types.ContentType.AUDIO, state=TeacherStates.waiting_for_audio)
async def process_audio(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    file_id = message.voice.file_id if message.voice else message.audio.file_id
    file_info = await bot.get_file(file_id)
    file_path = file_info.file_path
    await message.answer("⏳ Downloading audio file...")
    with tempfile.NamedTemporaryFile(delete=False, suffix='.ogg') as temp_file:
        downloaded_file = await bot.download_file(file_path, temp_file.name)
        await message.answer("Please enter a title for this lesson:")
        await TeacherStates.waiting_for_title.set()
        async with state.proxy() as data:
            data["audio_file_path"] = temp_file.name

@dp.message_handler(state=TeacherStates.waiting_for_title)
async def process_title(message: types.Message, state: FSMContext):
    title = message.text
    async with state.proxy() as data:
        data["title"] = title
    await message.answer("Please enter a brief description:")
    await TeacherStates.waiting_for_description.set()

@dp.message_handler(state=TeacherStates.waiting_for_description)
async def process_description(message: types.Message, state: FSMContext):
    description = message.text
    async with state.proxy() as data:
        data["description"] = description
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    buttons = [
        types.InlineKeyboardButton(text="Flashcards Only", callback_data="content_flashcards"),
        types.InlineKeyboardButton(text="Quiz Only", callback_data="content_quiz"),
        types.InlineKeyboardButton(text="Both Flashcards and Quiz", callback_data="content_both")
    ]
    keyboard.add(*buttons)
    await message.answer("What type of content would you like to generate?", reply_markup=keyboard)
    await TeacherStates.waiting_for_content_type.set()

@dp.callback_query_handler(lambda c: c.data.startswith("content_"), state=TeacherStates.waiting_for_content_type)
async def process_content_type(callback_query: types.CallbackQuery, state: FSMContext):
    content_type = callback_query.data.split("_")[1]
    async with state.proxy() as data:
        data["content_type"] = content_type
    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(callback_query.from_user.id, "How many items would you like to generate? (default: 10)")
    await TeacherStates.waiting_for_count.set()

@dp.message_handler(state=TeacherStates.waiting_for_count)
async def process_count_and_upload(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        count = int(message.text)
    except ValueError:
        count = 10
    async with state.proxy() as data:
        subject_id = data["subject_id"]
        audio_file_path = data["audio_file_path"]
        title = data["title"]
        description = data["description"]
        content_type = data["content_type"]
    await message.answer("⏳ Uploading audio file and generating content... This may take a minute.")
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {USER_SESSIONS[user_id]['access_token']}"}
        with open(audio_file_path, 'rb') as f:
            form_data = aiohttp.FormData()
            form_data.add_field('file', f)
            form_data.add_field('title', title)
            form_data.add_field('description', description)
            form_data.add_field('subject_id', str(subject_id))
            async with session.post(f"{API_BASE_URL}/api/core/upload/", data=form_data, headers=headers) as response:
                if response.status == 201:
                    audio_data = await response.json()
                    audio_id = audio_data["id"]
                    gen_data = {
                        "input_type": "audio",
                        "content_type": content_type,
                        "audio_id": audio_id,
                        "count": count,
                        "subject_id": subject_id
                    }
                    async with session.post(f"{API_BASE_URL}/api/core/generate-ai-content/", json=gen_data, headers=headers) as gen_response:
                        if gen_response.status == 201:
                            gen_result = await gen_response.json()
                            success_msg = f"✅ Content generated successfully!\n\n"
                            if content_type in ["flashcards", "both"]:
                                flashcard_count = gen_result.get("flashcard_count", 0)
                                success_msg += f"📝 {flashcard_count} flashcards created\n"
                            if content_type in ["quiz", "both"]:
                                question_count = gen_result.get("question_count", 0)
                                success_msg += f"❓ {question_count} quiz questions created\n"
                            await message.answer(success_msg)
                            os.unlink(audio_file_path)
                            await state.finish()
                            await show_teacher_menu(message)
                        else:
                            error_data = await gen_response.json()
                            await message.answer(f"❌ Content generation failed: {error_data.get('error', 'Unknown error')}")
                else:
                    error_data = await response.json()
                    await message.answer(f"❌ Audio upload failed: {error_data.get('error', 'Unknown error')}")


# ----- Student Functionality -----

async def show_student_menu(message: types.Message):
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = [
        "📚 Learn New Flashcards",
        "🔄 Review Due Flashcards",
        "📊 View My Progress",
        "📋 Help"
    ]
    keyboard.add(*buttons)
    await message.answer("Student Dashboard - What would you like to do today?", reply_markup=keyboard)

@dp.message_handler(lambda message: message.text == "📚 Learn New Flashcards")
async def learn_new_flashcards_handler(message: types.Message):
    user_id = message.from_user.id
    if user_id not in USER_SESSIONS or "access_token" not in USER_SESSIONS[user_id]:
        await message.answer("Please login first.")
        return
    await message.answer("⏳ Loading new flashcards...")
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {USER_SESSIONS[user_id]['access_token']}"}
        async with session.get(f"{API_BASE_URL}/api/core/flashcards/new-cards/", headers=headers) as response:
            if response.status == 200:
                flashcards = await response.json()
                if not flashcards:
                    await message.answer("You don't have any new flashcards to learn. Try reviewing existing ones!")
                    return
                USER_SESSIONS[user_id]["current_flashcards"] = flashcards
                USER_SESSIONS[user_id]["current_index"] = 0
                await StudentStates.in_learning_session.set()
                await show_current_flashcard(message, user_id)
            else:
                await message.answer("❌ Failed to load flashcards. Please try again later.")

@dp.message_handler(lambda message: message.text == "🔄 Review Due Flashcards")
async def review_flashcards_handler(message: types.Message):
    user_id = message.from_user.id
    if user_id not in USER_SESSIONS or "access_token" not in USER_SESSIONS[user_id]:
        await message.answer("Please login first.")
        return
    await message.answer("⏳ Loading flashcards due for review...")
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {USER_SESSIONS[user_id]['access_token']}"}
        async with session.get(f"{API_BASE_URL}/api/core/flashcards/due-reviews/", headers=headers) as response:
            if response.status == 200:
                flashcards = await response.json()
                if not flashcards:
                    await message.answer("You don't have any flashcards due for review!")
                    return
                USER_SESSIONS[user_id]["current_flashcards"] = flashcards
                USER_SESSIONS[user_id]["current_index"] = 0
                await StudentStates.in_review_session.set()
                await show_current_flashcard(message, user_id)
            else:
                await message.answer("❌ Failed to load flashcards. Please try again later.")

async def show_current_flashcard(message: types.Message, user_id):
    flashcards = USER_SESSIONS[user_id]["current_flashcards"]
    current_index = USER_SESSIONS[user_id]["current_index"]
    if current_index >= len(flashcards):
        await message.answer("You've reviewed all the flashcards in this session! 🎉")
        await show_student_menu(message)
        return
    current_card = flashcards[current_index]
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton(text="Show Answer", callback_data="show_answer"),
        types.InlineKeyboardButton(text="Skip", callback_data="skip_card")
    ]
    keyboard.add(*buttons)
    await message.answer(
        f"📝 Card {current_index + 1}/{len(flashcards)}\n\n"
        f"<b>Definition:</b>\n{current_card['definition']}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@dp.callback_query_handler(lambda c: c.data == "show_answer", state=[StudentStates.in_learning_session, StudentStates.in_review_session])
async def show_answer_handler(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    flashcards = USER_SESSIONS[user_id]["current_flashcards"]
    current_index = USER_SESSIONS[user_id]["current_index"]
    current_card = flashcards[current_index]
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton(text="I knew it ✅", callback_data="knew_it"),
        types.InlineKeyboardButton(text="Still learning ❌", callback_data="learning")
    ]
    keyboard.add(*buttons)
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=f"📝 Card {current_index + 1}/{len(flashcards)}\n\n"
             f"<b>Definition:</b>\n{current_card['definition']}\n\n"
             f"<b>Term:</b>\n{current_card['term']}\n\n"
             f"Did you know this?",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@dp.callback_query_handler(lambda c: c.data == "skip_card", state=[StudentStates.in_learning_session, StudentStates.in_review_session])
async def skip_card_handler(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    USER_SESSIONS[user_id]["current_index"] += 1
    await bot.answer_callback_query(callback_query.id)
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await show_current_flashcard(callback_query.message, user_id)

@dp.callback_query_handler(lambda c: c.data == "knew_it", state=[StudentStates.in_learning_session, StudentStates.in_review_session])
async def knew_it_handler(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    flashcards = USER_SESSIONS[user_id]["current_flashcards"]
    current_index = USER_SESSIONS[user_id]["current_index"]
    current_card = flashcards[current_index]
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {USER_SESSIONS[user_id]['access_token']}"}
        if await state.get_state() == "StudentStates:in_learning_session":
            async with session.post(
                f"{API_BASE_URL}/api/core/flashcards/{current_card['id']}/swipe/",
                json={"direction": "left"},
                headers=headers
            ) as response:
                pass
        else:
            async with session.post(
                f"{API_BASE_URL}/api/core/flashcards/{current_card['id']}/review/",
                json={"was_correct": True},
                headers=headers
            ) as response:
                pass
    USER_SESSIONS[user_id]["current_index"] += 1
    await bot.answer_callback_query(callback_query.id)
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(callback_query.message.chat.id, "👍 Great job! Marked as known.")
    await show_current_flashcard(callback_query.message, user_id)

@dp.callback_query_handler(lambda c: c.data == "learning", state=[StudentStates.in_learning_session, StudentStates.in_review_session])
async def still_learning_handler(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    flashcards = USER_SESSIONS[user_id]["current_flashcards"]
    current_index = USER_SESSIONS[user_id]["current_index"]
    current_card = flashcards[current_index]
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {USER_SESSIONS[user_id]['access_token']}"}
        if await state.get_state() == "StudentStates:in_learning_session":
            async with session.post(
                f"{API_BASE_URL}/api/core/flashcards/{current_card['id']}/swipe/",
                json={"direction": "right"},
                headers=headers
            ) as response:
                pass
        else:
            async with session.post(
                f"{API_BASE_URL}/api/core/flashcards/{current_card['id']}/review/",
                json={"was_correct": False},
                headers=headers
            ) as response:
                pass
    USER_SESSIONS[user_id]["current_index"] += 1
    await bot.answer_callback_query(callback_query.id)
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await bot.send_message(callback_query.message.chat.id, "📝 No problem! This card will come back for review.")
    await show_current_flashcard(callback_query.message, user_id)

# ----- Quiz Functionality -----

@dp.message_handler(lambda message: message.text == "📝 Take Quiz")
async def take_quiz_handler(message: types.Message):
    user_id = message.from_user.id
    if user_id not in USER_SESSIONS or "access_token" not in USER_SESSIONS[user_id]:
        await message.answer("Please login first.")
        return
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {USER_SESSIONS[user_id]['access_token']}"}
        async with session.get(f"{API_BASE_URL}/api/core/all-quizzes/", headers=headers) as response:
            if response.status == 200:
                quizzes = await response.json()
                if not quizzes:
                    await message.answer("No quizzes available at the moment.")
                    return
                keyboard = types.InlineKeyboardMarkup(row_width=1)
                for quiz in quizzes:
                    keyboard.add(types.InlineKeyboardButton(
                        text=quiz["title"],
                        callback_data=f"quiz_{quiz['id']}"
                    ))
                await message.answer("Select a quiz to take:", reply_markup=keyboard)
            else:
                await message.answer("❌ Failed to load quizzes. Please try again later.")

@dp.callback_query_handler(lambda c: c.data.startswith("quiz_"))
async def start_quiz(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    quiz_id = callback_query.data.split("_")[1]
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {USER_SESSIONS[user_id]['access_token']}"}
        async with session.get(f"{API_BASE_URL}/api/core/quizzes/{quiz_id}/", headers=headers) as response:
            if response.status == 200:
                quiz = await response.json()
                USER_SESSIONS[user_id]["current_quiz"] = quiz
                USER_SESSIONS[user_id]["current_question"] = 0
                USER_SESSIONS[user_id]["correct_answers"] = 0
                await bot.answer_callback_query(callback_query.id)
                await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
                await bot.send_message(
                    callback_query.message.chat.id,
                    f"Starting quiz: {quiz['title']}\n\n{quiz.get('description', '')}"
                )
                await show_quiz_question(callback_query.message, user_id)
            else:
                await bot.answer_callback_query(callback_query.id, "Failed to load quiz. Please try again.")

async def show_quiz_question(message: types.Message, user_id):
    quiz = USER_SESSIONS[user_id]["current_quiz"]
    current_question_idx = USER_SESSIONS[user_id]["current_question"]
    if current_question_idx >= len(quiz["questions"]):
        correct = USER_SESSIONS[user_id]["correct_answers"]
        total = len(quiz["questions"])
        percentage = (correct / total) * 100
        await message.answer(
            f"🎉 Quiz completed!\n\n"
            f"You got {correct} out of {total} questions correct ({percentage:.1f}%)."
        )
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {USER_SESSIONS[user_id]['access_token']}"}
            data = {"quiz_id": quiz["id"], "score": percentage, "completed": True}
            async with session.post(f"{API_BASE_URL}/api/core/quizzes/{quiz['id']}/complete/", json=data, headers=headers) as response:
                pass
        await bot.send_message(message.chat.id, "Returning to the main menu.")
        await show_student_menu(message)
        return
    current_question = quiz["questions"][current_question_idx]
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for option in current_question["options"]:
        keyboard.add(types.InlineKeyboardButton(text=option, callback_data=f"option_{option}"))
    await message.answer(
        f"❓ Question {current_question_idx + 1}/{len(quiz['questions'])}\n\n"
        f"{current_question['question_text']}",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith("option_"))
async def quiz_answer_handler(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    quiz = USER_SESSIONS[user_id]["current_quiz"]
    current_question_idx = USER_SESSIONS[user_id]["current_question"]
    current_question = quiz["questions"][current_question_idx]
    selected_option = callback_query.data.split("_", 1)[1]
    if selected_option.strip().lower() == current_question["correct_answer"].strip().lower():
        USER_SESSIONS[user_id]["correct_answers"] += 1
    USER_SESSIONS[user_id]["current_question"] += 1
    await bot.answer_callback_query(callback_query.id)
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await show_quiz_question(callback_query.message, user_id)

if __name__ == '__main__':
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True)
