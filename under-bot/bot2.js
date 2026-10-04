// Telegram bot для Under - v6 FULL RESTORED + Groq AI (мультимодальная Qwen)
// Модерация ТОЛЬКО через ИИ, без ключевых слов. Короткие уведомления.
require('dotenv').config();

function safeRequire(name){
  try { return require(name); }
  catch(e){
    if(e.code === 'MODULE_NOT_FOUND'){
      console.error(`\n❌ Модуль не найден: ${name}`);
      console.error(`👉 Выполни в папке /app: npm install`);
      console.error(`   npm install node-telegram-bot-api firebase-admin axios dotenv groq-sdk\n`);
      process.exit(1);
    }
    throw e;
  }
}

const TelegramBot = safeRequire('node-telegram-bot-api');
const admin = safeRequire('firebase-admin');
const axios = safeRequire('axios');
const Groq = safeRequire('groq-sdk');

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const HARDCODED_DB_URL = "https://akanchik-id-default-rtdb.europe-west1.firebasedatabase.app";
const FIREBASE_DB_URL = process.env.FIREBASE_DB_URL || process.env.FIREBASE_DATABASE_URL || process.env.DATABASE_URL || HARDCODED_DB_URL;
console.log("DB_URL:", FIREBASE_DB_URL);
console.log("BOT_TOKEN:", (process.env.TELEGRAM_BOT_TOKEN||"").slice(0,10)+"...");
const SERVICE_ACCOUNT_PATH = process.env.GOOGLE_APPLICATION_CREDENTIALS || "./serviceAccountKey.json";
const APP_URL = process.env.APP_URL || "https://akanchik-id.github.io";
const ADMIN_TG_USERNAME = (process.env.ADMIN_TG_USERNAME || "razvyazcka").toLowerCase();
const GROQ_API_KEY = process.env.GROQ_API_KEY || "";

// Модели Groq:
// - для текста: openai/gpt-oss-safeguard-20b (или llama3-70b-8192)
// - для изображений: Qwen/Qwen3.6-27B (мультимодальная)
const TEXT_MODELS = [
  "openai/gpt-oss-safeguard-20b"
];
const VISION_MODELS = [
  "qwen/qwen3.8-27b"           // основная мультимодальная
];

let groq = null;
if (GROQ_API_KEY) {
  try {
    groq = new Groq({ apiKey: GROQ_API_KEY });
    console.log("✅ Groq AI подключен");
  } catch(e) {
    console.warn("⚠️ Ошибка инициализации Groq:", e.message);
  }
} else {
  console.warn("⚠️ GROQ_API_KEY не задан - Groq отключен, посты будут отправляться админу на проверку");
}

if(!BOT_TOKEN){
  console.error("❌ TELEGRAM_BOT_TOKEN не задан в .env");
  process.exit(1);
}

// Загрузка serviceAccount
let serviceAccount = null;
let loadedFrom = "";
try{
  const envJson = process.env.FIREBASE_SERVICE_ACCOUNT_JSON || process.env.FIREBASE_SERVICE_ACCOUNT || "";
  if(envJson){
    serviceAccount = JSON.parse(envJson);
    loadedFrom = "ENV JSON";
  }
} catch(e){ console.warn("ENV JSON parse err", e.message); }

if(!serviceAccount){
  const fs = require('fs');
  const paths = [SERVICE_ACCOUNT_PATH, "./serviceAccountKey.json", "/app/serviceAccountKey.json", __dirname+"/serviceAccountKey.json"];
  for(const p of paths){
    try{
      if(fs.existsSync(p)){
        serviceAccount = JSON.parse(fs.readFileSync(p,'utf8'));
        loadedFrom = p;
        break;
      }
    }catch(e){}
  }
}

if(!serviceAccount){
  console.error(`❌ Не найден serviceAccountKey.json`);
  console.error(`   Ищу в: ${SERVICE_ACCOUNT_PATH}, ./serviceAccountKey.json, /app/serviceAccountKey.json`);
  console.error(`   Скачай из Firebase Console > Project Settings > Service Accounts`);
  console.error(`   Или задай FIREBASE_SERVICE_ACCOUNT_JSON в .env`);
  process.exit(1);
}
console.log(`✅ Firebase cred загружен из: ${loadedFrom}`);

admin.initializeApp({
  credential: admin.credential.cert(serviceAccount),
  databaseURL: FIREBASE_DB_URL
});

const db = admin.database();
const bot = new TelegramBot(BOT_TOKEN, {polling: true});

console.log(`🤖 Under Telegram Bot v6 (admin @${ADMIN_TG_USERNAME}) запущен...`);

// ================= STICKER PACK =================
const STICKER_PACK_NAME = process.env.STICKER_PACK_NAME || "bogatiryata_connoreyka_by_fStikBot";
let stickerPackCache = [];

async function loadStickerPack(){
  try{
    console.log(`🔍 Загружаю стикерпак ${STICKER_PACK_NAME}...`);
    const set = await bot.getStickerSet(STICKER_PACK_NAME);
    if(set && set.stickers && set.stickers.length){
      stickerPackCache = set.stickers.map(s=>s.file_id);
      console.log(`✅ Стикерпак загружен: ${stickerPackCache.length} стикеров`);
    } else {
      console.log(`⚠️ Стикерпак ${STICKER_PACK_NAME} пуст или не найден`);
    }
  }catch(e){
    console.error(`❌ Не удалось загрузить стикерпак ${STICKER_PACK_NAME}:`, e.message);
    stickerPackCache = [];
  }
}

function parseStickerNumber(stickerUrl){
  try{
    const m = stickerUrl.match(/s(\d+)\.gif/i) || stickerUrl.match(/s(\d+)/i);
    if(m) return parseInt(m[1], 10);
    const m2 = stickerUrl.match(/(\d+)/);
    if(m2) return parseInt(m2[1],10);
    return null;
  }catch(e){ return null; }
}

async function sendTelegramPackSticker(chatId, stickerUrl, header){
  const num = parseStickerNumber(stickerUrl);
  if(num && stickerPackCache.length >= num && num >= 1){
    const fileId = stickerPackCache[num-1];
    try{
      await bot.sendSticker(chatId, fileId);
      if(header){
        await bot.sendMessage(chatId, header, {parse_mode: 'HTML', disable_web_page_preview: true});
      }
      return true;
    }catch(e){
      console.error(`Failed to send pack sticker #${num}:`, e.message);
    }
  }
  return false;
}

// ================= ONLINE CHECK =================
async function isUserOnline(uid){
  try{
    const presSnap = await db.ref(`Users/${uid}/Presence`).once('value');
    const pres = presSnap.val();
    if(pres){
      if(pres.state === 'online'){
        const diff = Date.now() - (pres.lastSeen||0);
        if(diff < 3*60*1000) return true;
      }
      if(pres.online === true) return true;
    }
    const onSnap = await db.ref(`Users/${uid}/isOnline`).once('value');
    if(onSnap.val() === true){
      const lastSnap = await db.ref(`Users/${uid}/lastActive`).once('value');
      const last = lastSnap.val() || 0;
      if(Date.now() - last < 5*60*1000) return true;
    }
    const lastSeenSnap = await db.ref(`Users/${uid}/lastSeen`).once('value');
    if(lastSeenSnap.exists() && Date.now() - lastSeenSnap.val() < 2*60*1000) return true;
    return false;
  }catch(e){ return false; }
}

async function sendTelegramIfOffline(uid, chatId, text, options={}){
  if(!chatId) return false;
  try{
    const online = await isUserOnline(uid);
    if(online){
      console.log(`[SKIP TG] ${uid} онлайн - не шлю в ТГ ${chatId}`);
      return false;
    }
    await bot.sendMessage(chatId, text, options);
    console.log(`[SEND TG] ${uid} -> ${chatId}`);
    return true;
  }catch(e){
    console.error("sendTelegramIfOffline error", e.message);
    return false;
  }
}

async function sendStickerIfOffline(uid, chatId, stickerId){
  if(!chatId) return false;
  const online = await isUserOnline(uid);
  if(online){
    console.log(`[SKIP TG STICKER] ${uid} онлайн`);
    return false;
  }
  try{
    await bot.sendSticker(chatId, stickerId);
    return true;
  }catch(e){ console.error("sticker send err", e.message); return false; }
}

// ================= КОРОТКИЕ УВЕДОМЛЕНИЯ =================
function getShortBanMessage({username, reason}){
  return `🚫 Аккаунт заблокирован

${reason || 'Нарушение правил.'}

Мы очень заботимся о безопасности наших пользователей.
Пожалуйста, не нарушайте правила сообщества Under.`;
}

function getShortWarnMessage({username, reason, warnCount}){
  return `⚠️ Предупреждение №${warnCount || 1}

${reason || 'Нарушение правил.'}

Мы очень заботимся о безопасности наших пользователей.
Пожалуйста, не нарушайте правила сообщества Under.`;
}

// ================= МОДЕРАЦИЯ ЧЕРЕЗ GROQ (МУЛЬТИМОДАЛЬНАЯ) =================
async function downloadImageAsBase64(url){
  try{
    if(!url) return null;
    if(typeof url === 'string' && url.startsWith('data:')){
      const m = url.match(/^data:(.*?);base64,(.*)$/);
      if(m){
        return { base64: m[2], mimeType: m[1] };
      }
      return { base64: url.split(',')[1] || '', mimeType: 'image/jpeg' };
    }
    if(typeof url !== 'string'){
      console.log("[MOD] photoUrl не строка, а", typeof url, url);
      return null;
    }
    if(url.length > 500 && !url.startsWith('http')){
      return { base64: url, mimeType: 'image/jpeg' };
    }
    const res = await axios.get(url, {responseType:'arraybuffer', timeout:20000});
    return { base64: Buffer.from(res.data).toString('base64'), mimeType: res.headers['content-type']||'image/jpeg' };
  }catch(e){ 
    console.error("download img error", e.message, "url:", (typeof url==='string'? url.slice(0,100) : url)); 
    return null; 
  }
}

// Анализ текста через текстовые модели
async function analyzeTextWithGroq(text) {
  if (!groq) return null;
  let lastError = null;
  for (const model of TEXT_MODELS) {
    try {
      const prompt = `Ты модератор соцсети Under. Проверь текст на запрещённый контент.
Категории: porn, drugs, weapons, gore, child, hate, violence.
Текст: "${text.slice(0, 1000)}"

Ответь строго JSON, без пояснений:
{"isBad": true/false, "category": "porn/drugs/weapons/gore/child/hate/violence/none", "reason": "коротко на русском", "confidence": 0.0-1.0, "shouldDelete": true/false, "shouldBan": true/false}`;

      const chatCompletion = await groq.chat.completions.create({
        messages: [{ role: "user", content: prompt }],
        model: model,
        temperature: 0,
        max_tokens: 1520,
      });

      const raw = chatCompletion.choices[0]?.message?.content || "";
      console.log(`[Groq text] (${model}) raw:`, raw.slice(0, 500));
      const firstBrace = raw.indexOf('{');
      const lastBrace = raw.lastIndexOf('}');
      if (firstBrace !== -1 && lastBrace !== -1 && lastBrace > firstBrace) {
        const jsonStr = raw.substring(firstBrace, lastBrace + 1);
        try {
          const parsed = JSON.parse(jsonStr);
          return parsed;
        } catch(e) {
          console.error(`Ошибка парсинга JSON (${model}):`, e.message, "строка:", jsonStr);
        }
      }
    } catch(e) {
      lastError = e;
      console.warn(`Ошибка с текстовой моделью ${model}:`, e.message);
    }
  }
  console.error("Все текстовые модели не сработали, последняя ошибка:", lastError?.message);
  return null;
}

// Анализ изображения + текст через мультимодальную модель
async function analyzeImageWithGroq({text, imageBase64, mimeType}) {
  if (!groq) return null;
  let lastError = null;
  for (const model of VISION_MODELS) {
    try {
      const prompt = `Ты модератор соцсети Under. Проверь пост на запрещённый контент. Учти и текст, и изображение.
Категории: porn, drugs, weapons, gore, child, hate, violence.
Текст: "${(text || '').slice(0, 1000)}"

Ответь строго JSON, без пояснений:
{"isBad": true/false, "category": "porn/drugs/weapons/gore/child/hate/violence/none", "reason": "коротко на русском", "confidence": 0.0-1.0, "shouldDelete": true/false, "shouldBan": true/false}`;

      const content = [
        { type: "text", text: prompt }
      ];
      if (imageBase64) {
        content.push({
          type: "image_url",
          image_url: { url: `data:${mimeType || 'image/jpeg'};base64,${imageBase64}` }
        });
      }

      const chatCompletion = await groq.chat.completions.create({
        messages: [{ role: "user", content: content }],
        model: model,
        temperature: 0,
        max_tokens: 850,
      });

      const raw = chatCompletion.choices[0]?.message?.content || "";
      console.log(`[Groq vision] (${model}) raw:`, raw.slice(0, 500));
      const firstBrace = raw.indexOf('{');
      const lastBrace = raw.lastIndexOf('}');
      if (firstBrace !== -1 && lastBrace !== -1 && lastBrace > firstBrace) {
        const jsonStr = raw.substring(firstBrace, lastBrace + 1);
        try {
          const parsed = JSON.parse(jsonStr);
          return parsed;
        } catch(e) {
          console.error(`Ошибка парсинга JSON (${model}):`, e.message, "строка:", jsonStr);
        }
      }
    } catch(e) {
      lastError = e;
      console.warn(`Ошибка с vision моделью ${model}:`, e.message);
    }
  }
  console.error("Все vision модели не сработали, последняя ошибка:", lastError?.message);
  return null;
}

// Главная функция анализа (Groq с поддержкой изображений)
async function analyzePostWithAI({text, imageBase64, mimeType}) {
  if (!text && !imageBase64) {
    return { isBad: false, category: 'none', reason: 'Пустой пост', confidence: 0 };
  }

  // Если есть изображение, используем мультимодальную модель
  if (imageBase64) {
    const result = await analyzeImageWithGroq({text, imageBase64, mimeType});
    if (result) {
      console.log("[AI] Использован Groq vision");
      return result;
    }
    // Если vision не сработал, пробуем текстовую модель (только текст)
    if (text && text.length > 3) {
      const textResult = await analyzeTextWithGroq(text);
      if (textResult) {
        console.log("[AI] Использован Groq text (fallback)");
        return textResult;
      }
    }
    // Если ничего не вышло – отправляем на ревью
    console.log("[AI] Не удалось проанализировать изображение, отправляем на ревью");
    return {
      isBad: false,
      category: 'image',
      reason: 'Не удалось обработать изображение, требуется ручная проверка',
      confidence: 0,
      needsReview: true
    };
  }

  // Если только текст – используем текстовые модели
  if (text && text.length > 3) {
    const result = await analyzeTextWithGroq(text);
    if (result) {
      console.log("[AI] Использован Groq text");
      return result;
    }
  }

  // Если ничего не сработало – отправляем на ревью
  console.log("[AI] Все методы не дали результата, пост отправлен на ревью админу");
  return {
    isBad: false,
    category: 'unknown',
    reason: 'AI недоступен или не распознал, требуется ручная проверка',
    confidence: 0,
    needsReview: true
  };
}

// Генерация короткого предупреждения через Groq (если получится)
async function generateAIWarningText({username, category, originalText, warnCount}){
  if (groq) {
    for (const model of TEXT_MODELS) {
      try {
        const prompt = `Напиши короткое предупреждение для пользователя ${username} о нарушении ${category}. Только причина и просьба не нарушать. Без лишних деталей.`;
        const chatCompletion = await groq.chat.completions.create({
          messages: [{ role: "user", content: prompt }],
          model: model,
          temperature: 0.5,
          max_tokens: 150
        });
        const t = chatCompletion.choices[0]?.message?.content || "";
        if (t && t.length > 20) return t;
      } catch(e) { console.warn("Ошибка генерации предупреждения:", e.message); }
    }
  }
  // Если Groq не дал ответ – используем шаблон
  return getShortWarnMessage({username, reason: `Нарушение: ${category}`, warnCount});
}

// ================= СЛУШАТЕЛЬ ПОСТОВ =================
let postModerationStartTime = Date.now();

async function setupPostModerationListener(){
  console.log(`[MOD] Слушаю новые посты с ${new Date(postModerationStartTime).toLocaleString()}...`);
  const postsRef = db.ref('Posts');
  postsRef.orderByChild('time').startAt(postModerationStartTime).on('child_added', async (snap)=>{
    const postId = snap.key;
    const post = snap.val();
    if(!post) return;
    if(post.time && post.time < postModerationStartTime - 120000) return;
    const authorUid = post.uid || post.author || post.userId;
    if(!authorUid) return;
    console.log(`[NEW POST] ${postId} by ${authorUid}`);

    const text = post.text || post.caption || post.desc || '';
    let photoUrl = null;
    if (post.photo) {
      if (typeof post.photo === 'string') photoUrl = post.photo;
      else if (typeof post.photo === 'object') {
        photoUrl = post.photo.url || post.photo.downloadURL || null;
      }
    }
    if (!photoUrl && post.photoUrl && typeof post.photoUrl === 'string') photoUrl = post.photoUrl;
    if (!photoUrl && post.image && typeof post.image === 'string') photoUrl = post.image;
    if (!photoUrl && post.img && typeof post.img === 'string') photoUrl = post.img;
    if (!photoUrl && Array.isArray(post.photos) && post.photos.length) {
      const first = post.photos[0];
      if (typeof first === 'string') photoUrl = first;
      else if (typeof first === 'object' && (first.url || first.downloadURL)) photoUrl = first.url || first.downloadURL;
    }
    if (post.photo && !photoUrl) {
      console.log(`[MOD] Не удалось определить URL фото в посте ${postId}, тип photo:`, typeof post.photo, post.photo);
    }

    if(!text && !photoUrl) return;

    let imgData = null;
    if(photoUrl){
      console.log(`[MOD] Парсинг ${postId}: text="${text.slice(0,80)}" photo=${!!photoUrl} type=${typeof photoUrl}`);
      imgData = await downloadImageAsBase64(photoUrl);
      if(!imgData){
        console.log(`[MOD] Не удалось скачать фото ${postId}, проверяю только текст`);
      }
    } else {
      console.log(`[MOD] Парсинг ${postId}: text="${text.slice(0,80)}" photo=false - только текст`);
    }

    let analysis = null;
    try{
      analysis = await analyzePostWithAI({text, imageBase64: imgData?.base64, mimeType: imgData?.mimeType});
    }catch(e){
      console.error(`[MOD] analyzePostWithAI threw`, e.message);
      analysis = {isBad:false, category:'error', reason: e.message, confidence:0};
    }

    if(!analysis){
      analysis = {isBad:false, category:'null', reason:'analysis null', confidence:0};
    }

    console.log(`[AI RESULT] ${postId} -> isBad=${analysis?.isBad} cat=${analysis?.category} conf=${analysis?.confidence} reason=${analysis?.reason}`);

    // Если нужен ревью – уведомляем админа и не удаляем
    if (analysis.needsReview) {
      await sendToAdmin(`⚠️ Подозрительный пост от @${authorUid}\nТекст: ${text || 'фото'}\nПричина: ${analysis.reason}\nID: ${postId}`);
      return;
    }

    if(analysis && analysis.isBad && (analysis.confidence||0) >= 0.55){
      console.log(`[BAD CONTENT] Удаляю ${postId} cat=${analysis.category}`);
      try{
        // Удаление
        if(photoUrl){
          await db.ref(`Posts/${postId}/photo`).remove();
          await db.ref(`Posts/${postId}/photoUrl`).remove();
          await db.ref(`Posts/${postId}/image`).remove();
          await db.ref(`Posts/${postId}/img`).remove();
          await db.ref(`Posts/${postId}/photos`).remove();
          await db.ref(`Posts/${postId}/moderated`).set(true);
          await db.ref(`Posts/${postId}/moderationReason`).set(`${analysis.category}: ${analysis.reason}`);
          await db.ref(`Posts/${postId}/moderatedAt`).set(Date.now());
        } else {
          await db.ref(`Posts/${postId}`).remove();
          await db.ref(`UserPosts/${authorUid}/${postId}`).remove();
        }

        // Данные юзера
        const uSnap = await db.ref(`Users/${authorUid}`).once('value');
        const uData = uSnap.val()||{};
        const username = uData.DisplayName || uData.username || 'пользователь';
        const tgId = uData.telegramChatId;

        // Увеличение счётчика варнов
        const wSnap = await db.ref(`Users/${authorUid}/warnCount`).once('value');
        const cur = (wSnap.val()||0)+1;
        await db.ref(`Users/${authorUid}/warnCount`).set(cur);
        await db.ref(`Users/${authorUid}/lastWarn`).set({reason: analysis.reason, category: analysis.category, time: Date.now(), postId});

        // Короткое предупреждение
        const warnText = await generateAIWarningText({username, category: analysis.category, originalText: text || analysis.reason, warnCount: cur});
        const warnTextShort = warnText.length < 300 ? warnText : getShortWarnMessage({username, reason: `Нарушение: ${analysis.category}`, warnCount: cur});

        // Системный чат (короткое сообщение)
        const sysChatId = `system_${authorUid}`;
        await db.ref(`Chats/${sysChatId}/messages`).push().set({
          text: warnTextShort,
          type: 'system',
          systemType: 'warn',
          reason: analysis.reason,
          category: analysis.category,
          postId,
          time: Date.now(),
          from: 'SYSTEM'
        });
        await db.ref(`UserChats/${authorUid}/${sysChatId}`).update({
          lastMessage: '⚠️ Предупреждение',
          lastTime: Date.now(),
          unread: admin.database.ServerValue.increment(1)
        });

        // Отправка в ТГ
        if(tgId){
          await sendTelegramIfOffline(authorUid, tgId, warnTextShort, {parse_mode:'HTML'});
        }

        // Автобан при 3+ варнах или shouldBan
        if(cur >= 3 || analysis.shouldBan || analysis.category === 'child'){
          const banReason = analysis.category === 'child' ? 'Детская эксплуатация (пермач)' : `3 предупреждения. Последнее: ${analysis.category}`;
          await db.ref(`Users/${authorUid}/banned`).set(true);
          await db.ref(`Users/${authorUid}/banReason`).set(banReason);
          await db.ref(`Users/${authorUid}/bannedAt`).set(Date.now());
          if(analysis.category !== 'child'){
            await db.ref(`Users/${authorUid}/bannedUntil`).set(Date.now()+7*24*60*60*1000);
          } else {
            await db.ref(`Users/${authorUid}/bannedUntil`).set(null);
          }

          const banMsg = getShortBanMessage({username, reason: banReason});

          await db.ref(`Chats/${sysChatId}/messages`).push().set({
            text: banMsg,
            type: 'system',
            systemType: 'ban',
            time: Date.now(),
            from: 'SYSTEM'
          });

          if(tgId) await sendTelegramIfOffline(authorUid, tgId, banMsg, {parse_mode:'HTML'});
        }

      }catch(e){ console.error("[MOD] handling error", e); }
    }
  });
}

// ================= I18N =================
const I18N = {
  ru: {
    welcome_no_code: `👋 Привет! Я бот уведомлений для <b>Under</b>

Чтобы подключить уведомления:
1. Открой Under
2. Зайди в Настройки → Уведомления
3. Нажми "Подключить Telegram"

Команды:
/status - проверить статус
/sessions - мои сессии
/unlink - отключить
/test - тест
/lang - язык
/help - помощь`,
    code_not_found: (code)=>`❌ Код <code>${code}</code> не найден или истёк.`,
    code_expired: `⏰ Код истёк.`,
    linked_ok: `✅ <b>Аккаунт подключен!</b>`,
    not_linked: `❌ Аккаунт не связан.`,
    status_title: `📊 <b>Статус</b>`,
    uid: `UID`,
    name: `Имя`,
    username: `Username`,
    notifs: `Уведомления`,
    connected_at: `Подключен`,
    enabled_on: `Включены ✅`,
    enabled_off: `Выключены ❌`,
    already_disabled: `Аккаунт и так не связан.`,
    disabled_ok: `✅ Отключено.`,
    test_ok: (time)=>`✅ <b>Тест!</b> Время: ${time}`,
    help: `🆘 <b>Помощь</b>
/status - статус
/sessions - сессии и устройства
/test - тест
/unlink - отключить
/lang - язык
/help - справка
/admin - админка (только @${ADMIN_TG_USERNAME})`,
    lang_changed_ru: `✅ Язык Русский`,
    lang_changed_en: `✅ Language English`,
    lang_select: `Выбери язык:`,
    new_message: `💬 Новое сообщение`,
    from: `от`,
    in_group: `в`,
    new_fan: `стал твоим фанатом`,
    liked_post: `лайкнул твой`,
    commented: `прокомментировал`,
    commented_post: `твой`,
    post_word: `пост`,
    user_word: `пользователь`,
    system: `Система`,
    test_msg: `Тестовое уведомление!`,
    photo: `📷 Фото`,
    voice: `🎙 Голосовое`,
    sticker: `🩵 Стикер`,
    post: `📋 Пост`,
    login_request_title: `🔐 <b>Новый вход в аккаунт</b>`,
    login_request_device: `Устройство`,
    login_request_browser: `Браузер`,
    login_request_platform: `Платформа`,
    login_request_time: `Время`,
    login_request_approve: `✅ Принять`,
    login_request_decline: `❌ Отклонить`,
    login_request_approved: `✅ Вход подтвержден!`,
    login_request_declined: `❌ Вход отклонен.`,
    login_request_expired: `⏰ Запрос истек.`,
    sessions_title: `📱 <b>Твои сессии</b>`,
    sessions_empty: `Сессий нет`,
    sessions_active: `Активные сессии:`,
    trusted_title: `🔒 Доверенные устройства:`,
    no_trusted: `Нет доверенных`,
    admin_only: `❌ Только для админа @${ADMIN_TG_USERNAME}`,
    admin_panel: `👑 <b>Админ панель</b>`,
    user_not_found: `❌ Пользователь не найден`,
    banned_ok: `✅ Пользователь забанен`,
    unbanned_ok: `✅ Разбанен`,
    warned_ok: `✅ Предупреждение отправлено`,
    bot_set_ok: `✅ Bot флаг изменен`,
    report_new: `🚨 <b>Новая жалоба</b>`,
    report_from: `От`,
    report_to: `На`,
    report_reason: `Причина`,
    report_comment: `Описание`,
    report_time: `Время`,
    ban_message: `🚫 <b>Ваш аккаунт заблокирован</b>`,
    warn_message: `⚠️ <b>Предупреждение</b>`,
    long_ban_template: getShortBanMessage,
    long_warn_template: getShortWarnMessage,
    restrict_message: `🔇 <b>Ограничение</b>`
  },
  en: {
    welcome_no_code: `👋 Hi! I'm bot for <b>Under</b>`,
    code_not_found: (code)=>`❌ Code <code>${code}</code> not found`,
    code_expired: `⏰ Code expired`,
    linked_ok: `✅ <b>Connected!</b>`,
    not_linked: `❌ Not linked`,
    status_title: `📊 <b>Status</b>`,
    uid: `UID`,
    name: `Name`,
    username: `Username`,
    notifs: `Notifications`,
    connected_at: `Connected`,
    enabled_on: `Enabled ✅`,
    enabled_off: `Disabled ❌`,
    already_disabled: `Already disabled`,
    disabled_ok: `✅ Disabled`,
    test_ok: (time)=>`✅ Test! Time: ${time}`,
    help: `Help /status /sessions /test /unlink /lang /help`,
    lang_changed_ru: `✅ Русский`,
    lang_changed_en: `✅ English`,
    lang_select: `Choose language:`,
    new_message: `💬 New message`,
    from: `from`,
    in_group: `in`,
    new_fan: `became fan`,
    liked_post: `liked your`,
    commented: `commented`,
    commented_post: `your`,
    post_word: `post`,
    user_word: `user`,
    system: `System`,
    test_msg: `Test notification!`,
    photo: `📷 Photo`,
    voice: `🎙 Voice`,
    sticker: `🩵 Sticker`,
    post: `📋 Post`,
    login_request_title: `🔐 <b>New login</b>`,
    login_request_device: `Device`,
    login_request_browser: `Browser`,
    login_request_platform: `Platform`,
    login_request_time: `Time`,
    login_request_approve: `✅ Accept`,
    login_request_decline: `❌ Decline`,
    login_request_approved: `✅ Approved!`,
    login_request_declined: `❌ Declined.`,
    login_request_expired: `⏰ Expired.`,
    sessions_title: `📱 <b>Your sessions</b>`,
    sessions_empty: `No sessions`,
    sessions_active: `Active:`,
    trusted_title: `🔒 Trusted:`,
    no_trusted: `No trusted`,
    admin_only: `❌ Admin only @${ADMIN_TG_USERNAME}`,
    admin_panel: `👑 <b>Admin</b>`,
    user_not_found: `❌ Not found`,
    banned_ok: `✅ Banned`,
    unbanned_ok: `✅ Unbanned`,
    warned_ok: `✅ Warned`,
    bot_set_ok: `✅ Bot flag changed`,
    report_new: `🚨 <b>New report</b>`,
    report_from: `From`,
    report_to: `To`,
    report_reason: `Reason`,
    report_comment: `Comment`,
    report_time: `Time`,
    ban_message: `🚫 <b>Banned</b>`,
    warn_message: `⚠️ <b>Warning</b>`,
    restrict_message: `🔇 <b>Restricted</b>`
  }
};

function t(lang, key, ...args){
  const tr = I18N[lang] && I18N[lang][key] ? I18N[lang][key] : (I18N['ru'][key] || key);
  return typeof tr === 'function' ? tr(...args) : tr;
}
function escapeHtml(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function getLangCode(msg, userData){
  if(userData && userData.telegramLang) return userData.telegramLang;
  if(msg && msg.from && msg.from.language_code){
    return msg.from.language_code.startsWith('en') ? 'en' : 'ru';
  }
  return 'ru';
}
function isAdminUser(msg){
  const uname = (msg.from && msg.from.username) ? msg.from.username.toLowerCase() : '';
  return uname === ADMIN_TG_USERNAME.toLowerCase();
}

// ================= USER LOOKUP =================
async function findUserByIdentifier(identifier){
  if(!identifier) return null;
  const clean = identifier.replace(/^@/, '').toLowerCase().trim();
  let snap = await db.ref(`Users/${identifier}`).once('value');
  if(snap.exists()){
    return {uid: identifier, data: snap.val()};
  }
  snap = await db.ref('Users').orderByChild('username').equalTo(clean).once('value');
  if(snap.exists()){
    let found = null;
    snap.forEach(child=>{ found = {uid: child.key, data: child.val()}; });
    return found;
  }
  const unameSnap = await db.ref(`Usernames/${clean}`).once('value');
  if(unameSnap.exists()){
    const val = unameSnap.val();
    if(val && val.uid){
      const uSnap = await db.ref(`Users/${val.uid}`).once('value');
      if(uSnap.exists()){
        return {uid: val.uid, data: uSnap.val()};
      }
    }
  }
  try{
    const allSnap = await db.ref('Users').once('value');
    let best = null;
    allSnap.forEach(child=>{
      const data = child.val();
      if(!data) return;
      const uname = (data.username||'').toLowerCase();
      const dname = (data.DisplayName||'').toLowerCase();
      if(uname === clean || dname === clean){
        best = {uid: child.key, data};
      } else if(!best && (uname.includes(clean) || dname.includes(clean))){
        best = {uid: child.key, data};
      }
    });
    if(best) return best;
  }catch(e){}
  return null;
}

async function searchUsersByName(query, limit=10){
  const clean = query.toLowerCase().trim();
  if(!clean) return [];
  const results = [];
  try{
    const snap = await db.ref('Users').once('value');
    snap.forEach(child=>{
      if(results.length >= limit) return;
      const data = child.val();
      if(!data) return;
      const uname = (data.username||'').toLowerCase();
      const dname = (data.DisplayName||'').toLowerCase();
      if(uname.includes(clean) || dname.includes(clean)){
        results.push({uid: child.key, data});
      }
    });
  }catch(e){}
  return results;
}

// ================= NOTIF FORMATTING =================
async function getMessageForNotification(chatId, fromUid, notifTime){
  try{
    const msgsSnap = await db.ref(`Chats/${chatId}/messages`).orderByChild('time').startAt(notifTime - 60000).endAt(notifTime + 60000).once('value');
    let found = null;
    msgsSnap.forEach(child=>{
      const m = child.val();
      if(m && m.from === fromUid && Math.abs((m.time||0) - notifTime) < 5000){
        found = m;
      }
    });
    return found;
  }catch(e){ return null; }
}

async function formatNotification(n, uid, userData, lang){
  try{
    const fromName = n.fromName || n.from || 'Unknown';
    const fromUid = n.from;
    const fromUsername = n.fromUsername || '';
    let header = '';
    let text = n.text || '';
    
    const profileLink = (name, uname, uidVal) => {
      const display = escapeHtml(name || uname || 'Пользователь');
      if(uname){
        return `<a href="${APP_URL}/user/${encodeURIComponent(uname)}">${display}</a>`;
      } else if(uidVal){
        return `<a href="${APP_URL}/user/${uidVal}">${display}</a>`;
      }
      return `<b>${display}</b>`;
    };
    
    const postLink = (postId) => {
      if(postId){
        return `<a href="${APP_URL}/post/${postId}">пост</a>`;
      }
      return 'пост';
    };
    
    const chatLink = async (chatId) => {
      if(!chatId) return null;
      try{
        const chatSnap = await db.ref(`Chats/${chatId}/info`).once('value');
        if(chatSnap.exists()){
          const info = chatSnap.val();
          if(info && info.name){
            const uname = info.username;
            const type = info.type === 'channel' ? 'channel' : 'group';
            if(uname){
              return `<a href="${APP_URL}/${type}/${encodeURIComponent(uname)}">${escapeHtml(info.name)}</a>`;
            }
            return `<b>${escapeHtml(info.name)}</b>`;
          }
        }
      }catch(e){}
      return null;
    };
    
    if(n.type === 'message'){
      const pLink = profileLink(fromName, fromUsername, fromUid);
      const cLink = await chatLink(n.chatId);
      if(cLink){
        header = `${t(lang,'new_message')} ${t(lang,'from')} ${pLink} ${t(lang,'in_group')} ${cLink}`;
      } else {
        header = `${t(lang,'new_message')} ${t(lang,'from')} ${pLink}`;
      }
    } else if(n.type === 'fan'){
      const pLink = profileLink(fromName, fromUsername, fromUid);
      header = `${pLink} ${t(lang,'new_fan')}`;
    } else if(n.type === 'like'){
      const pLink = profileLink(fromName, fromUsername, fromUid);
      header = `${pLink} ${t(lang,'liked_post')} ${postLink(n.postId)}`;
    } else if(n.type === 'comment'){
      const pLink = profileLink(fromName, fromUsername, fromUid);
      header = `${pLink} ${t(lang,'commented')} ${postLink(n.postId)}`;
    } else if(n.type === 'test'){
      header = t(lang,'test_msg');
      text = '';
    } else if(n.type === 'warn'){
      header = `⚠️ <b>Предупреждение</b>`;
      text = n.text || '';
    } else if(n.type === 'ban'){
      header = `🚫 <b>Блокировка</b>`;
      text = n.text || '';
    } else if(n.type === 'restrict'){
      header = `🔇 <b>Ограничение</b>`;
      text = n.text || '';
    } else if(n.type === 'login_approved'){
      header = `✅ <b>Вход подтвержден</b>`;
      text = n.text || '';
    } else if(n.type === 'login_declined'){
      header = `❌ <b>Вход отклонен</b>`;
      text = n.text || '';
    } else {
      const pLink = profileLink(fromName, fromUsername, fromUid);
      header = pLink;
    }
    return {header, text};
  }catch(e){
    console.error('formatNotification error', e);
    return null;
  }
}

async function sendActualMessage(chatId, actualMsg, headerInfo, lang){
  try{
    const type = actualMsg.type || 'text';
    if(type === 'photo' && actualMsg.photoUrl){
      await bot.sendPhoto(chatId, actualMsg.photoUrl, {caption: headerInfo.header, parse_mode:'HTML'});
    } else if(type === 'sticker'){
      const sent = await sendTelegramPackSticker(chatId, actualMsg.sticker || actualMsg.text, headerInfo.header);
      if(!sent){
        await bot.sendMessage(chatId, `${headerInfo.header}\n\n${headerInfo.text || actualMsg.text || ''}`, {parse_mode:'HTML'});
      }
    } else if(type === 'voice' && actualMsg.voiceUrl){
      await bot.sendVoice(chatId, actualMsg.voiceUrl, {caption: headerInfo.header, parse_mode:'HTML'});
    } else {
      const fullText = headerInfo.text ? `${headerInfo.header}\n\n${headerInfo.text}` : headerInfo.header;
      await bot.sendMessage(chatId, fullText, {parse_mode:'HTML'});
    }
  }catch(e){
    console.error('sendActualMessage error', e);
    const fullText = headerInfo.text ? `${headerInfo.header}\n\n${headerInfo.text}` : headerInfo.header;
    await bot.sendMessage(chatId, fullText, {parse_mode:'HTML'});
  }
}

// ================= LISTENERS =================
const activeListeners = new Map();
const alreadySentCache = new Map();

function alreadySent(uid, key){
  if(!alreadySentCache.has(uid)) alreadySentCache.set(uid, new Set());
  const set = alreadySentCache.get(uid);
  if(set.has(key)) return true;
  set.add(key);
  if(set.size > 1000){
    const arr = Array.from(set);
    alreadySentCache.set(uid, new Set(arr.slice(-500)));
  }
  return false;
}

async function attachListener(uid, chatId){
  if(activeListeners.has(uid)){
    const old = activeListeners.get(uid);
    try{ old.ref.off('child_added', old.cb); }catch(e){}
  }
  const ref = db.ref(`Notifications/${uid}`);
  const existingKeys = new Set();
  try{
    const snap = await ref.once('value');
    snap.forEach(child=> existingKeys.add(child.key));
  }catch(e){}

  const cb = async (childSnap)=>{
    const key = childSnap.key;
    try{
      if(existingKeys.has(key)) return;
      if(alreadySent(uid, key)) return;
      const n = childSnap.val();
      if(!n) return;
      if(n.read) return;
      if(n.type !== 'test' && n.time && Date.now() - n.time > 60*60*1000) return;
      
      const userSnap = await db.ref(`Users/${uid}`).once('value');
      const userData = userSnap.val() || {};
      if(userData.telegramEnabled === false) return;
      if(userData.telegramChatId !== chatId) return;

      const online = await isUserOnline(uid);
      if(online){
        console.log(`[SKIP TG NOTIF] ${uid} онлайн, пропускаю уведомление ${key} type=${n ? n.type : 'unknown'}`);
        return;
      }

      const fakeMsg = {from:{language_code: userData.telegramLang === 'en' ? 'en' : 'ru'}};
      const lang = getLangCode(fakeMsg, userData);

      if(n.type === 'login_request'){
        try{
          const device = n.device || {};
          const sessionId = n.sessionId;
          if(!sessionId) return;
          const sessionSnap = await db.ref(`Users/${uid}/loginSessions/${sessionId}`).once('value');
          if(!sessionSnap.exists()) return;
          const sessionData = sessionSnap.val();
          if(sessionData.status !== 'pending') return;
          const text = `${t(lang,'login_request_title')}

<b>${t(lang,'login_request_device')}:</b> ${escapeHtml(device.platform || 'Unknown')} ${escapeHtml(device.screen || '')}
<b>${t(lang,'login_request_browser')}:</b> ${escapeHtml(device.browser || 'Unknown')}
<b>${t(lang,'login_request_platform')}:</b> ${escapeHtml(device.platform || 'Unknown')}
<b>${t(lang,'login_request_time')}:</b> ${escapeHtml(device.time || new Date(n.time).toLocaleString())}

<code>Session: ${escapeHtml(sessionId)}</code>`;

          await bot.sendMessage(chatId, text, {
            parse_mode: 'HTML',
            reply_markup: {
              inline_keyboard: [[
                {text: t(lang,'login_request_approve'), callback_data: `approve_login_${uid}_${sessionId}`},
                {text: t(lang,'login_request_decline'), callback_data: `decline_login_${uid}_${sessionId}`}
              ]]
            }
          });
          console.log(`🔐 Login request sent to ${uid} session ${sessionId}`);
          return;
        }catch(e){
          console.error('Login request error', e);
          return;
        }
      }

      const headerInfo = await formatNotification(n, uid, userData, lang);
      if(!headerInfo) return;

      if(n.type === 'message' && n.chatId){
        const actualMsg = await getMessageForNotification(n.chatId, n.from, n.time);
        if(actualMsg && actualMsg.type && actualMsg.type !== 'text'){
          await sendActualMessage(chatId, actualMsg, headerInfo, lang);
          console.log(`📤 [media:${actualMsg.type}] Sent to ${uid}`);
          return;
        }
      }

      const fullText = headerInfo.text ? `${headerInfo.header}\n\n${headerInfo.text}` : headerInfo.header;
      await bot.sendMessage(chatId, fullText, {parse_mode: 'HTML', disable_web_page_preview: true});
      console.log(`📤 [text] Sent ${n.type} to ${uid}`);
    }catch(e){
      console.error('Send error', e.message);
    }
  };

  ref.on('child_added', cb);
  activeListeners.set(uid, {chatId, ref, cb, existingKeys});
  console.log(`👂 Listening ${uid} -> ${chatId}`);
}

async function detachListener(uid){
  if(!activeListeners.has(uid)) return;
  const {ref, cb} = activeListeners.get(uid);
  try{ ref.off('child_added', cb); }catch(e){}
  activeListeners.delete(uid);
}

async function loadAllLinkedUsers(){
  console.log("🔍 Загружаю связанных пользователей...");
  try{
    const snap = await db.ref('Users').once('value');
    let count=0;
    const promises=[];
    snap.forEach(child=>{
      const data = child.val();
      if(data && data.telegramChatId){
        promises.push(attachListener(child.key, data.telegramChatId).catch(e=>console.log(`attach err ${child.key}`, e.message)));
        count++;
      }
    });
    await Promise.all(promises);
    console.log(`✅ Подключено ${count} пользователей`);
  }catch(e){ console.error('loadAllLinkedUsers error', e.message); }
}

// ================= ADMIN HELPERS =================
async function getAdminChatId(){
  try{
    const snap = await db.ref('Users').orderByChild('telegramUsername').equalTo(ADMIN_TG_USERNAME).once('value');
    if(snap.exists()){
      let chatId = null;
      snap.forEach(child=>{
        const data = child.val();
        if(data.telegramChatId) chatId = data.telegramChatId;
      });
      if(chatId) return chatId;
    }
    const allSnap = await db.ref('Users').once('value');
    let found = null;
    allSnap.forEach(child=>{
      const data = child.val();
      if(data.telegramUsername && data.telegramUsername.toLowerCase() === ADMIN_TG_USERNAME.toLowerCase() && data.telegramChatId){
        found = data.telegramChatId;
      }
    });
    return found;
  }catch(e){ return null; }
}

async function sendToAdmin(text, opts={}){
  const adminChatId = await getAdminChatId();
  if(!adminChatId){
    console.log('Admin chat not found for @'+ADMIN_TG_USERNAME);
    return false;
  }
  try{
    await bot.sendMessage(adminChatId, text, {parse_mode:'HTML', disable_web_page_preview:true, ...opts});
    return true;
  }catch(e){
    console.error('sendToAdmin error', e.message);
    return false;
  }
}

// === SYSTEM CHAT ON SITE ===
async function ensureSystemChat(uid){
  const chatId = `system_${uid}`;
  const infoRef = db.ref(`Chats/${chatId}/info`);
  const snap = await infoRef.once('value');
  if(!snap.exists()){
    await infoRef.set({
      name: 'Система',
      username: 'system',
      isSystem: true,
      type: 'system',
      photoUrl: `${APP_URL}/logo.png`,
      createdAt: Date.now()
    });
  }
  const userChatRef = db.ref(`UserChats/${uid}/${chatId}`);
  const ucSnap = await userChatRef.once('value');
  if(!ucSnap.exists()){
    await userChatRef.set({
      lastText: 'Служебные уведомления',
      lastTime: Date.now(),
      unread: 0
    });
  }
  return chatId;
}

async function sendSystemChatMessage(uid, text, extra={}){
  try{
    const chatId = await ensureSystemChat(uid);
    const msgKey = db.ref(`Chats/${chatId}/messages`).push().key;
    const msgData = {
      from: 'system',
      text: text,
      time: Date.now(),
      type: extra.type || 'system',
      systemType: extra.systemType || 'info',
      fromName: 'Система',
      ...extra
    };
    await db.ref(`Chats/${chatId}/messages/${msgKey}`).set(msgData);
    await db.ref(`UserChats/${uid}/${chatId}`).update({
      lastText: text.substring(0,60),
      lastTime: Date.now(),
      unread: admin.database.ServerValue.increment(1)
    });
    console.log(`📢 System chat message sent to ${uid}: ${text.substring(0,40)}`);
    return chatId;
  }catch(e){
    console.error('sendSystemChatMessage error', e);
  }
}

// ================= КОМАНДЫ =================
bot.onText(/\/start(?:\s+(.+))?/, async (msg, match)=>{
  const chatId = msg.chat.id;
  const code = match[1] ? match[1].trim() : null;
  const username = msg.from.username || '';
  console.log(`▶️ /start from ${chatId} @${username} code=${code}`);
  if(!code){
    const lang = msg.from.language_code && msg.from.language_code.startsWith('en') ? 'en' : 'ru';
    await bot.sendMessage(chatId, t(lang,'welcome_no_code'), {parse_mode:'HTML'});
    return;
  }
  try{
    const usersSnap = await db.ref('Users').orderByChild('telegramLinkCode').equalTo(code.toLowerCase()).once('value');
    if(!usersSnap.exists()){
      const lang = msg.from.language_code && msg.from.language_code.startsWith('en') ? 'en' : 'ru';
      await bot.sendMessage(chatId, t(lang,'code_not_found', code), {parse_mode:'HTML'});
      return;
    }
    let linkedUid = null, userData = null;
    usersSnap.forEach(child=>{ linkedUid = child.key; userData = child.val(); });
    if(userData.telegramLinkExpires && Date.now() > userData.telegramLinkExpires){
      const lang = getLangCode(msg, userData);
      await bot.sendMessage(chatId, t(lang,'code_expired'));
      return;
    }
    const lang = getLangCode(msg, userData);
    await db.ref(`Users/${linkedUid}`).update({
      telegramChatId: chatId,
      telegramUsername: username,
      telegramLinkedAt: Date.now(),
      telegramEnabled: true,
      telegramLinkCode: null,
      telegramLinkExpires: null,
      telegramLang: lang
    });
    await attachListener(linkedUid, chatId);
    await bot.sendMessage(chatId, t(lang,'linked_ok'), {parse_mode:'HTML'});
    console.log(`✅ Linked ${linkedUid} <-> ${chatId} @${username}`);
  }catch(e){
    console.error(e);
    await bot.sendMessage(chatId, `❌ Error: ${e.message}`);
  }
});

bot.onText(/\/status/, async (msg)=>{
  const chatId = msg.chat.id;
  try{
    const snap = await db.ref('Users').orderByChild('telegramChatId').equalTo(chatId).once('value');
    if(!snap.exists()){
      const lang = msg.from.language_code && msg.from.language_code.startsWith('en') ? 'en' : 'ru';
      await bot.sendMessage(chatId, t(lang,'not_linked'));
      return;
    }
    let uid=null, data=null;
    snap.forEach(c=>{ uid=c.key; data=c.val(); });
    const lang = getLangCode(msg, data);
    const enabled = data.telegramEnabled !== false ? t(lang,'enabled_on') : t(lang,'enabled_off');
    await bot.sendMessage(chatId, 
`${t(lang,'status_title')}

${t(lang,'uid')}: <code>${uid}</code>
${t(lang,'name')}: ${escapeHtml(data.DisplayName||'PLAYER')}
${t(lang,'username')}: @${escapeHtml(data.username||'???')}
${t(lang,'notifs')}: ${enabled}
${t(lang,'connected_at')}: ${new Date(data.telegramLinkedAt||Date.now()).toLocaleString(lang==='ru'?'ru-RU':'en-US')}

Commands:
/sessions - sessions
/test - test
/unlink - disconnect
/lang - language
`, {parse_mode:'HTML'});
  }catch(e){ bot.sendMessage(chatId, `Error: ${e.message}`); }
});

bot.onText(/\/sessions/, async (msg)=>{
  const chatId = msg.chat.id;
  try{
    const snap = await db.ref('Users').orderByChild('telegramChatId').equalTo(chatId).once('value');
    if(!snap.exists()){
      await bot.sendMessage(chatId, `❌ Аккаунт не связан`);
      return;
    }
    let uid=null, data=null;
    snap.forEach(c=>{ uid=c.key; data=c.val(); });
    const lang = getLangCode(msg, data);
    const sessSnap = await db.ref(`Users/${uid}/loginSessions`).once('value');
    const trustedSnap = await db.ref(`Users/${uid}/trustedDevices`).once('value');
    let text = `${t(lang,'sessions_title')}\n\n`;
    if(sessSnap.exists()){
      text += `${t(lang,'sessions_active')}\n`;
      sessSnap.forEach(child=>{
        const s = child.val();
        const time = s.time ? new Date(s.time).toLocaleString() : 'unknown';
        const status = s.status || 'unknown';
        const browser = s.browser || s.device?.browser || 'unknown';
        const platform = s.platform || s.device?.platform || 'unknown';
        text += `• <code>${child.key}</code> - ${escapeHtml(browser)} / ${escapeHtml(platform)} - ${status} - ${time}\n`;
      });
    } else {
      text += `${t(lang,'sessions_empty')}\n`;
    }
    text += `\n${t(lang,'trusted_title')}\n`;
    if(trustedSnap.exists()){
      trustedSnap.forEach(child=>{
        const d = child.val();
        const time = d.lastUsed ? new Date(d.lastUsed).toLocaleString() : (d.createdAt ? new Date(d.createdAt).toLocaleString() : 'unknown');
        const fp = child.key;
        const dev = d.device || {};
        text += `• <code>${escapeHtml(fp.substring(0,12))}</code> - ${escapeHtml(dev.browser||'?')} / ${escapeHtml(dev.platform||'?')} - ${time}\n`;
      });
    } else {
      text += `${t(lang,'no_trusted')}\n`;
    }
    text += `\nИспользуй /unlink чтобы отвязать, или удали доверенные через базу`;
    await bot.sendMessage(chatId, text, {parse_mode:'HTML'});
  }catch(e){ bot.sendMessage(chatId, `Error: ${e.message}`); }
});

bot.onText(/\/unlink/, async (msg)=>{
  const chatId = msg.chat.id;
  try{
    const snap = await db.ref('Users').orderByChild('telegramChatId').equalTo(chatId).once('value');
    if(!snap.exists()){
      const lang = msg.from.language_code && msg.from.language_code.startsWith('en') ? 'en' : 'ru';
      await bot.sendMessage(chatId, t(lang,'already_disabled'));
      return;
    }
    let uid=null, data=null;
    snap.forEach(c=>{ uid=c.key; data=c.val(); });
    const lang = getLangCode(msg, data);
    await db.ref(`Users/${uid}`).update({telegramChatId: null, telegramUsername: null, telegramEnabled: null});
    await detachListener(uid);
    await bot.sendMessage(chatId, t(lang,'disabled_ok'));
  }catch(e){ bot.sendMessage(chatId, `Error: ${e.message}`); }
});

bot.onText(/\/test/, async (msg)=>{
  const chatId = msg.chat.id;
  const lang = msg.from.language_code && msg.from.language_code.startsWith('en') ? 'en' : 'ru';
  await bot.sendMessage(chatId, t(lang,'test_ok', new Date().toLocaleString(lang==='ru'?'ru-RU':'en-US')), {parse_mode:'HTML'});
});

bot.onText(/\/lang/, async (msg)=>{
  const chatId = msg.chat.id;
  const lang = msg.from.language_code && msg.from.language_code.startsWith('en') ? 'en' : 'ru';
  await bot.sendMessage(chatId, t(lang,'lang_select'), {
    reply_markup: {inline_keyboard: [[{text: '🇷🇺 Русский', callback_data: 'set_lang_ru'}, {text: '🇬🇧 English', callback_data: 'set_lang_en'}]]}
  });
});

bot.onText(/\/help/, async (msg)=>{
  const chatId = msg.chat.id;
  try{
    const snap = await db.ref('Users').orderByChild('telegramChatId').equalTo(chatId).once('value');
    let data=null;
    snap.forEach(c=>{ data=c.val(); });
    const lang = getLangCode(msg, data);
    let helpText = t(lang,'help');
    if(isAdminUser(msg)){
      helpText += `

👑 <b>Админ команды:</b>
/admin - панель
/sessions [@user] - сессии юзера
/ban @user причина - забанить
/unban @user - разбанить
/warn @user причина - предупреждение
/setbot @user true/false - сделать ботом
/reports - жалобы
/userinfo @user - инфа о юзере
/restrict @user причина - ограничить
/clear_sessions @user - очистить сессии`;
    }
    await bot.sendMessage(chatId, helpText, {parse_mode:'HTML'});
  }catch(e){
    const lang = msg.from.language_code && msg.from.language_code.startsWith('en') ? 'en' : 'ru';
    await bot.sendMessage(chatId, t(lang,'help'), {parse_mode:'HTML'});
  }
});

// ================= АДМИН КОМАНДЫ =================
bot.onText(/\/admin/, async (msg)=>{
  if(!isAdminUser(msg)){
    await bot.sendMessage(msg.chat.id, I18N.ru.admin_only);
    return;
  }
  const text = `${I18N.ru.admin_panel} @${ADMIN_TG_USERNAME}

<b>Команды:</b>
/sessions [@username] - сессии
/ban @username причина - бан
/unban @username - разбан
/warn @username причина - варн
/setbot @username true/false
/userinfo @username
/reports - список жалоб
/restrict @username причина
/clear_sessions @username
/clear_trusted @username

<b>Баны:</b> Пишутся в Users/{uid}/banned (только через бота, юзеры не могут изменить)
<b>Жалобы:</b> Приходят сюда автоматом`;

  await bot.sendMessage(msg.chat.id, text, {
    parse_mode:'HTML',
    reply_markup: {
      inline_keyboard: [
        [{text: '📋 Жалобы', callback_data: 'admin_reports'}, {text: '👤 Инфо юзера', callback_data: 'admin_userinfo_prompt'}],
        [{text: '🚫 Баны', callback_data: 'admin_banned_list'}, {text: '⚠️ Варны', callback_data: 'admin_warns'}]
      ]
    }
  });
});

bot.onText(/\/userinfo\s+(.+)/, async (msg, match)=>{
  if(!isAdminUser(msg)){
    await bot.sendMessage(msg.chat.id, I18N.ru.admin_only);
    return;
  }
  const identifier = match[1].trim();
  const found = await findUserByIdentifier(identifier);
  if(!found){
    await bot.sendMessage(msg.chat.id, I18N.ru.user_not_found + ` ${escapeHtml(identifier)}`);
    return;
  }
  const data = found.data;
  const banned = data.banned ? `🚫 ЗАБАНЕН: ${escapeHtml(data.banReason||'no reason')}` : '✅ Не забанен';
  const botFlag = data.bot ? '🤖 Бот: Да' : '👤 Бот: Нет';
  const warns = data.warnings ? Object.keys(data.warnings).length : 0;
  const text = `👤 <b>Инфо</b> @${escapeHtml(data.username||'?')}

UID: <code>${found.uid}</code>
Имя: ${escapeHtml(data.DisplayName||'?')}
Username: @${escapeHtml(data.username||'?')}
${botFlag}
${banned}
Варны: ${warns}
Монеты: ${data.Coins||0}
Фанаты: ${data.Fans ? Object.keys(data.Fans).length : 0}
Кумиры: ${data.Idols ? Object.keys(data.Idols).length : 0}
Telegram: ${data.telegramChatId ? '✅ @'+escapeHtml(data.telegramUsername||'?') : '❌'}
Создан: ${data.createdAt ? new Date(data.createdAt).toLocaleString() : '?'}

Команды:
/ban ${identifier} причина
/warn ${identifier} причина
/setbot ${identifier} true`;

  await bot.sendMessage(msg.chat.id, text, {parse_mode:'HTML'});
});

bot.onText(/\/ban\s+@?(\S+)(?:\s+(.+))?/, async (msg, match)=>{
  if(!isAdminUser(msg)){
    await bot.sendMessage(msg.chat.id, I18N.ru.admin_only);
    return;
  }
  const identifier = match[1];
  const reason = match[2] || 'No reason';
  const found = await findUserByIdentifier(identifier);
  if(!found){
    await bot.sendMessage(msg.chat.id, I18N.ru.user_not_found);
    return;
  }
  await db.ref(`Users/${found.uid}`).update({
    banned: true,
    banReason: reason,
    bannedAt: Date.now(),
    bannedBy: ADMIN_TG_USERNAME,
    telegramEnabled: false
  });
  if(found.data.telegramChatId){
    try{
      await bot.sendMessage(found.data.telegramChatId, getShortBanMessage({username: found.data.username, reason}), {parse_mode:'HTML'});
    }catch(e){}
  }
  try{
    const notifKey = db.ref(`Notifications/${found.uid}`).push().key;
    await db.ref(`Notifications/${found.uid}/${notifKey}`).set({
      type: 'warn',
      from: 'system',
      fromName: 'Система',
      fromUsername: 'system',
      fromAvatar: 1,
      text: reason,
      time: Date.now(),
      read: false
    });
  }catch(e){}
  await sendSystemChatMessage(found.uid, getShortBanMessage({username: found.data.username, reason}), {systemType: 'warn', reason});
  await bot.sendMessage(msg.chat.id, `${I18N.ru.warned_ok} @${escapeHtml(found.data.username||identifier)}: ${escapeHtml(reason)}`, {parse_mode:'HTML'});
});

bot.onText(/\/setbot\s+@?(\S+)\s+(true|false|1|0)/i, async (msg, match)=>{
  if(!isAdminUser(msg)){
    await bot.sendMessage(msg.chat.id, I18N.ru.admin_only);
    return;
  }
  const identifier = match[1];
  const val = match[2].toLowerCase();
  const isBot = val === 'true' || val === '1';
  const found = await findUserByIdentifier(identifier);
  if(!found){
    await bot.sendMessage(msg.chat.id, I18N.ru.user_not_found);
    return;
  }
  await db.ref(`Users/${found.uid}`).update({bot: isBot});
  await bot.sendMessage(msg.chat.id, `${I18N.ru.bot_set_ok} @${escapeHtml(found.data.username||identifier)} = ${isBot}`, {parse_mode:'HTML'});
});

bot.onText(/\/restrict\s+@?(\S+)(?:\s+(.+))?/, async (msg, match)=>{
  if(!isAdminUser(msg)){
    await bot.sendMessage(msg.chat.id, I18N.ru.admin_only);
    return;
  }
  const identifier = match[1];
  const reason = match[2] || 'Restricted by admin';
  const found = await findUserByIdentifier(identifier);
  if(!found){
    await bot.sendMessage(msg.chat.id, I18N.ru.user_not_found);
    return;
  }
  await db.ref(`Users/${found.uid}/privacy`).update({whoCanMsg: 'none'});
  await db.ref(`Users/${found.uid}/restricted`).set({
    reason: reason,
    by: ADMIN_TG_USERNAME,
    time: Date.now()
  });
  if(found.data.telegramChatId){
    try{
      await bot.sendMessage(found.data.telegramChatId, `${I18N.ru.restrict_message}

<b>Причина:</b> ${escapeHtml(reason)}`, {parse_mode:'HTML'});
    }catch(e){}
  }
  await bot.sendMessage(msg.chat.id, `🔇 Ограничен @${escapeHtml(found.data.username||identifier)}: ${escapeHtml(reason)}`, {parse_mode:'HTML'});
});

bot.onText(/\/clear_sessions\s+@?(\S+)/, async (msg, match)=>{
  if(!isAdminUser(msg)){
    await bot.sendMessage(msg.chat.id, I18N.ru.admin_only);
    return;
  }
  const identifier = match[1];
  const found = await findUserByIdentifier(identifier);
  if(!found){
    await bot.sendMessage(msg.chat.id, I18N.ru.user_not_found);
    return;
  }
  await db.ref(`Users/${found.uid}/loginSessions`).remove();
  await bot.sendMessage(msg.chat.id, `✅ Сессии очищены для @${escapeHtml(found.data.username||identifier)}`, {parse_mode:'HTML'});
});

bot.onText(/\/clear_trusted\s+@?(\S+)/, async (msg, match)=>{
  if(!isAdminUser(msg)){
    await bot.sendMessage(msg.chat.id, I18N.ru.admin_only);
    return;
  }
  const identifier = match[1];
  const found = await findUserByIdentifier(identifier);
  if(!found){
    await bot.sendMessage(msg.chat.id, I18N.ru.user_not_found);
    return;
  }
  await db.ref(`Users/${found.uid}/trustedDevices`).remove();
  await bot.sendMessage(msg.chat.id, `✅ Доверенные устройства очищены для @${escapeHtml(found.data.username||identifier)}`, {parse_mode:'HTML'});
});

bot.onText(/\/reports/, async (msg)=>{
  if(!isAdminUser(msg)){
    await bot.sendMessage(msg.chat.id, I18N.ru.admin_only);
    return;
  }
  try{
    const snap = await db.ref('Reports').orderByChild('time').limitToLast(20).once('value');
    if(!snap.exists()){
      await bot.sendMessage(msg.chat.id, `📋 Жалоб нет`);
      return;
    }
    let text = `📋 <b>Последние 20 жалоб:</b>\n\n`;
    const reports = [];
    snap.forEach(child=>{
      reports.push({key: child.key, ...child.val()});
    });
    reports.reverse().forEach(r=>{
      const time = r.time ? new Date(r.time).toLocaleString() : '?';
      text += `🆘 <code>${r.key.substring(0,6)}</code> ${escapeHtml(r.reason||'?')} - от ${r.from ? r.from.substring(0,6) : '?'} на ${r.target ? r.target.substring(0,6) : (r.postId ? 'пост '+r.postId.substring(0,6) : '?')}\n`;
      if(r.comment) text += `   "${escapeHtml(r.comment.substring(0,50))}"\n`;
      text += `   ${time}\n\n`;
    });
    await bot.sendMessage(msg.chat.id, text, {parse_mode:'HTML'});
  }catch(e){
    await bot.sendMessage(msg.chat.id, `Error: ${e.message}`);
  }
});

// ================= REPORTS LISTENER =================
db.ref('Reports').on('child_added', async (snap)=>{
  const report = snap.val();
  if(!report) return;
  if(report.time && Date.now() - report.time > 60*1000) return;
  try{
    let fromInfo = 'Unknown';
    let toInfo = 'Unknown';
    if(report.from){
      const fromSnap = await db.ref(`Users/${report.from}`).once('value');
      if(fromSnap.exists()){
        const d = fromSnap.val();
        fromInfo = `@${d.username||'?'} (${d.DisplayName||'?'}) UID:${report.from.substring(0,6)}`;
      } else {
        fromInfo = report.from.substring(0,8);
      }
    }
    if(report.target){
      const toSnap = await db.ref(`Users/${report.target}`).once('value');
      if(toSnap.exists()){
        const d = toSnap.val();
        toInfo = `@${d.username||'?'} (${d.DisplayName||'?'}) UID:${report.target.substring(0,6)}`;
      } else {
        toInfo = report.target.substring(0,8);
      }
    } else if(report.postId){
      toInfo = `Пост ${report.postId.substring(0,8)}`;
    }
    const text = `${I18N.ru.report_new}

<b>${I18N.ru.report_from}:</b> ${escapeHtml(fromInfo)}
<b>${I18N.ru.report_to}:</b> ${escapeHtml(toInfo)}
<b>${I18N.ru.report_reason}:</b> ${escapeHtml(report.reason||'?')}
${report.comment ? `<b>${I18N.ru.report_comment}:</b> ${escapeHtml(report.comment)}\n` : ''}
${report.postId ? `<b>PostID:</b> <code>${report.postId}</code>\n` : ''}
<b>${I18N.ru.report_time}:</b> ${new Date(report.time||Date.now()).toLocaleString()}

ID: <code>${snap.key}</code>

Действия:
/userinfo ${report.target||report.from}
 /ban ${report.target||''} причина
 /warn ${report.target||''} причина`;

    await sendToAdmin(text);
    console.log(`🚨 Report forwarded to admin: ${snap.key}`);
  }catch(e){
    console.error('Report forward error', e);
  }
});

// ================= CALLBACK QUERY =================
bot.on('callback_query', async (q)=>{
  const chatId = q.message.chat.id;
  const data = q.data;
  
  if(data.startsWith('approve_login_') || data.startsWith('decline_login_')){
    try{
      const isApprove = data.startsWith('approve_login_');
      const rest = data.replace('approve_login_','').replace('decline_login_','');
      const idx = rest.indexOf('_');
      const uid = rest.substring(0, idx);
      const sessionId = rest.substring(idx+1);
      console.log(`🔐 Login ${isApprove ? 'approve' : 'decline'} ${uid} ${sessionId}`);
      const sessionRef = db.ref(`Users/${uid}/loginSessions/${sessionId}`);
      const snap = await sessionRef.once('value');
      if(!snap.exists()){
        await bot.answerCallbackQuery(q.id, {text: 'Session not found'});
        return;
      }
      const sessionData = snap.val();
      if(sessionData.status !== 'pending'){
        await bot.answerCallbackQuery(q.id, {text: `Already ${sessionData.status}`});
        return;
      }
      await sessionRef.update({status: isApprove ? 'approved' : 'declined', decidedAt: Date.now(), decidedBy: chatId});
      const userSnap = await db.ref(`Users/${uid}`).once('value');
      const userData = userSnap.val() || {};
      const fakeMsg = {from:{language_code: userData.telegramLang === 'en' ? 'en' : 'ru'}};
      const lang = getLangCode(fakeMsg, userData);
      await bot.answerCallbackQuery(q.id, {text: isApprove ? 'Approved ✓' : 'Declined'});
      const resultText = isApprove ? t(lang,'login_request_approved') : t(lang,'login_request_declined');
      await bot.editMessageText(`${q.message.text}\n\n${resultText}`, {chat_id: chatId, message_id: q.message.message_id, parse_mode:'HTML'});
      if(isApprove){
        const sessSnap = await db.ref(`Users/${uid}/loginSessions/${sessionId}`).once('value');
        const sessData = sessSnap.val() || {};
        const dev = sessData.device || {};
        await sendSystemChatMessage(uid, `✅ Вход в аккаунт подтвержден

Устройство: ${dev.browser||'?'} / ${dev.platform||'?'}
Время: ${new Date().toLocaleString()}
Сессия: ${sessionId}

Если это были вы — все в порядке, можете не предпринимать никаких действий.

Если вы не входили в аккаунт, рекомендуем немедленно сменить пароль и проверить активные сессии в настройках безопасности.

Это автоматическое уведомление системы безопасности Under.`, {systemType: 'login_approved', sessionId});
        try{
          const notifKey = db.ref(`Notifications/${uid}`).push().key;
          await db.ref(`Notifications/${uid}/${notifKey}`).set({
            type: 'login_approved',
            from: 'system',
            fromName: 'Система',
            fromUsername: 'system',
            text: `Вход подтвержден: ${dev.browser||'?'} / ${dev.platform||'?'} ${sessionId}`,
            time: Date.now(),
            read: false
          });
        }catch(e){}
      } else {
        await sendSystemChatMessage(uid, `❌ Попытка входа отклонена

Сессия: ${sessionId}
Время: ${new Date().toLocaleString()}

Кто-то пытался войти в ваш аккаунт, но запрос был отклонен.

Если это были не вы, рекомендуем сменить пароль и проверить доверенные устройства в настройках.

Если это были вы и вы случайно отклонили запрос — просто попробуйте войти снова.`, {systemType: 'login_declined', sessionId});
      }
      if(!isApprove){
        await bot.sendMessage(chatId, `🔒 If this wasn't you, change your password!`, {parse_mode:'HTML'});
      }
      return;
    }catch(e){
      console.error('Login callback error', e);
      await bot.answerCallbackQuery(q.id, {text: 'Error'}).catch(()=>{});
    }
    return;
  }
  
  if(data==='set_lang_ru' || data==='set_lang_en'){
    const newLang = data==='set_lang_ru' ? 'ru' : 'en';
    try{
      const snap = await db.ref('Users').orderByChild('telegramChatId').equalTo(chatId).once('value');
      if(snap.exists()){
        let uid=null;
        snap.forEach(c=>{ uid=c.key; });
        await db.ref(`Users/${uid}`).update({telegramLang: newLang});
      }
      await bot.answerCallbackQuery(q.id, {text: newLang==='ru' ? 'Русский' : 'English'});
      await bot.sendMessage(chatId, t(newLang, newLang==='ru' ? 'lang_changed_ru' : 'lang_changed_en'));
    }catch(e){ await bot.answerCallbackQuery(q.id, {text: 'Error'}); }
    return;
  }
  
  if(data==='admin_reports'){
    if(!isAdminUser({from:{username: q.from.username}})){
      await bot.answerCallbackQuery(q.id, {text: 'Admin only'});
      return;
    }
    await bot.answerCallbackQuery(q.id, {text: 'Loading reports...'});
    const snap = await db.ref('Reports').orderByChild('time').limitToLast(10).once('value');
    if(!snap.exists()){
      await bot.sendMessage(chatId, `📋 Жалоб нет`);
      return;
    }
    let text = `📋 <b>Последние жалобы:</b>\n\n`;
    snap.forEach(child=>{
      const r = child.val();
      text += `• ${escapeHtml(r.reason||'?')} - ${r.time ? new Date(r.time).toLocaleString() : ''}\n`;
    });
    await bot.sendMessage(chatId, text, {parse_mode:'HTML'});
    return;
  }
  
  if(data==='admin_userinfo_prompt'){
    if(!isAdminUser({from:{username: q.from.username}})){
      await bot.answerCallbackQuery(q.id, {text: 'Admin only'});
      return;
    }
    await bot.answerCallbackQuery(q.id, {text: 'Send /userinfo @username'});
    await bot.sendMessage(chatId, `Отправь: /userinfo @username или UID`);
    return;
  }
});

// ================= СЛУШАТЕЛИ ИЗМЕНЕНИЙ ПОЛЬЗОВАТЕЛЕЙ =================
db.ref('Users').on('child_changed', async (snap)=>{
  const uid = snap.key;
  const data = snap.val();
  if(!data) return;
  const prev = activeListeners.get(uid);
  if(data.telegramChatId && !prev){
    await attachListener(uid, data.telegramChatId);
  } else if(!data.telegramChatId && prev){
    await detachListener(uid);
  } else if(data.telegramChatId && prev && prev.chatId !== data.telegramChatId){
    await detachListener(uid);
    await attachListener(uid, data.telegramChatId);
  }
});

db.ref('Users').on('child_added', async (snap)=>{
  const uid = snap.key;
  const data = snap.val();
  if(data && data.telegramChatId && !activeListeners.has(uid)){
    await attachListener(uid, data.telegramChatId);
  }
});

// ================= ЗАПУСК =================
Promise.all([loadAllLinkedUsers(), loadStickerPack()]).then(()=>{
  console.log(`🚀 Bot v6 ready - admin @${ADMIN_TG_USERNAME}, AI: Groq (text: ${TEXT_MODELS[0]}, vision: ${VISION_MODELS[0]})`);
  setupPostModerationListener().catch(e=>console.error("Post listener error", e));
});

process.on('SIGINT', ()=>{
  console.log("Stopping bot...");
  bot.stopPolling();
  process.exit(0);
});