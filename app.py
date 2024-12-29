from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from typing import Optional, Dict
import logging
from dataclasses import dataclass
import os
from dotenv import load_dotenv
load_dotenv()

# 設置日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 配置類
@dataclass
class BotConfig:
    ACCESS_TOKEN: str = os.getenv("ACCESS_TOKEN")
    CHANNEL_SECRET: str = os.getenv("CHANNEL_SECRET")
    SOURCE_GROUP_ID: str = os.getenv("SOURCE_GROUP_ID")
    TARGET_GROUP_ID: str = os.getenv("TARGET_GROUP_ID")
    START_KEYWORD: str = '【Cashier Notifier】'
    OTHER_KEYWORDS: list = None

    def __post_init__(self):
        self.OTHER_KEYWORDS = ['錯誤碼：', 'video:']

class LineBot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.configuration = Configuration(access_token=config.ACCESS_TOKEN)
        self.handler = WebhookHandler(config.CHANNEL_SECRET)
        self.setup_handler()

    def setup_handler(self):
        @self.handler.add(MessageEvent, message=TextMessageContent)
        def handle_message(event):
            self.process_message(event)

    def should_forward_message(self, message_text: str) -> bool:
        """檢查訊息是否需要轉發"""
        if not message_text.lower().startswith(self.config.START_KEYWORD.lower()):
            return False
        
        return all(
            keyword.lower() in message_text.lower()
            for keyword in self.config.OTHER_KEYWORDS
        )

    def get_group_info(self, api: MessagingApi, group_id: str) -> Dict:
        """獲取群組資訊"""
        try:
            group_summary = api.get_group_summary(group_id)
            group_members_count = api.get_group_member_count(group_id)
            return {
                'summary': group_summary,
                'members_count': group_members_count
            }
        except Exception as e:
            logger.error(f"獲取群組資訊失敗: {str(e)}")
            raise

    def create_message_text(self, message_type: str, sender_name: str, 
                          group_name: str, original_text: str) -> str:
        """創建訊息文本"""
        templates = {
            'forward': "[轉發訊息]\n發送者：{sender}\nFrom群組：{group}\n訊息內容：\n\n{text}",
            'reply': "[訊息已轉發]\n發送者：{sender}\nTo群組：{group}\n訊息內容：\n\n{text}"
        }
        return templates[message_type].format(
            sender=sender_name,
            group=group_name,
            text=original_text
        )

    def process_message(self, event):
        """處理訊息事件"""
        try:
            # 檢查訊息來源
            if event.source.type == 'user' or \
               event.source.group_id != self.config.SOURCE_GROUP_ID:
                return

            # 檢查是否需要轉發
            if not self.should_forward_message(event.message.text):
                return

            with ApiClient(self.configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                
                # 獲取群組和發送者資訊
                source_group = self.get_group_info(line_bot_api, event.source.group_id)
                sender_profile = line_bot_api.get_group_member_profile(
                    event.source.group_id,
                    event.source.user_id
                )
                target_group = self.get_group_info(line_bot_api, self.config.TARGET_GROUP_ID)

                # 創建並發送轉發訊息
                forward_text = self.create_message_text(
                    'forward',
                    sender_profile.display_name,
                    source_group['summary'].group_name,
                    event.message.text
                )
                line_bot_api.push_message(
                    PushMessageRequest(
                        to=self.config.TARGET_GROUP_ID,
                        messages=[TextMessage(text=forward_text)]
                    )
                )

                # 創建並發送回覆訊息
                reply_text = self.create_message_text(
                    'reply',
                    sender_profile.display_name,
                    target_group['summary'].group_name,
                    event.message.text
                )
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)]
                    )
                )

                logger.info(f"訊息轉發成功: {event.message.text[:50]}...")

        except Exception as e:
            logger.error(f"處理訊息失敗: {str(e)}")
            # 可以在這裡添加失敗通知機制

# Flask 應用
app = Flask(__name__)
bot = LineBot(BotConfig())

@app.route("/", methods=['GET', 'POST'])
def health_check():
    """健康檢查端點"""
    return 'Service is running'

@app.route("/callback", methods=['POST'])
def callback():
    """Webhook 回調端點"""
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    logger.info("Received webhook")
    logger.debug(f"Request body: {body}")

    try:
        bot.handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("Invalid signature")
        abort(400)
    except Exception as e:
        logger.error(f"Webhook處理失敗: {str(e)}")
        abort(500)

    return 'OK'

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)