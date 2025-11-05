from typing import Optional

"""
偶像大师官网新闻推送插件
定期抓取官网新闻并推送到群聊
"""
import os
import json
import asyncio
from bs4 import BeautifulSoup
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# AstrBot API 导入
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

# Selenium 相关
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    logger.warning("Selenium 未安装，插件功能可能受限")

# httpx 用于异步 HTTP 请求（替代 aiorequests）
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    logger.warning("httpx 未安装，请运行: pip install httpx")

# 常量定义
IMAS_NEWS_URL = 'https://idolmaster-official.jp/news'


@register(
    "imas_news_notification",
    "Soulter",
    "偶像大师官网新闻推送插件，定期抓取并推送最新新闻",
    "1.0.0"
)
class ImasNewsPlugin(Star):
    """偶像大师官网新闻推送插件"""
    
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        
        # 缓存路径 - 使用 AstrBot 的 data 目录
        self.cache_dir = os.path.join(get_astrbot_data_path(), 'imas_news')
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # 图片保存路径
        self.img_dir = os.path.join(self.cache_dir, 'images')
        os.makedirs(self.img_dir, exist_ok=True)
        
        # 缓存数据
        self.idx_cache = set()  # 新闻 ID 缓存
        self.item_cache = []    # 新闻内容缓存（最多保存5条）
        
        # 定时任务调度器
        self.scheduler = AsyncIOScheduler()
        
    async def initialize(self):
        """插件初始化 - 加载缓存并启动定时任务"""
        self._load_cache()
        
        # 添加定时任务：每分钟检查一次新闻更新
        self.scheduler.add_job(
            self._check_news_update,
            trigger="cron",
            minute="*/1",  # 每分钟执行一次
            id="imas_news_check"
        )
        self.scheduler.start()
        logger.info("IM@S 新闻推送插件已启动，定时任务已开始")
        
    async def terminate(self):
        """插件销毁 - 保存缓存并停止定时任务"""
        self._save_cache()
        if self.scheduler.running:
            self.scheduler.shutdown()
        logger.info("IM@S 新闻推送插件已停止")


    
    def _load_cache(self):
        """加载缓存的新闻索引"""
        cache_file = os.path.join(self.cache_dir, 'cache.json')
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r', encoding='utf8') as f:
                    data = json.load(f)
                    self.idx_cache = set(data.get('idx_cache', []))
                    self.item_cache = data.get('item_cache', [])
                    logger.info(f'成功加载缓存，共 {len(self.idx_cache)} 条新闻')
            except Exception as e:
                logger.error(f'加载缓存失败: {e}')
    
    def _save_cache(self):
        """保存缓存的新闻索引"""
        cache_file = os.path.join(self.cache_dir, 'cache.json')
        try:
            with open(cache_file, 'w', encoding='utf8') as f:
                json.dump({
                    'idx_cache': list(self.idx_cache),
                    'item_cache': self.item_cache
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f'保存缓存失败: {e}')
    
    async def _download_image(self, url: str, save_name: str) -> bool:
        """下载图片到本地"""
        if not HTTPX_AVAILABLE:
            logger.error("httpx 未安装，无法下载图片")
            return False
            
        save_path = os.path.join(self.img_dir, save_name)
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=30)
                if resp.status_code == 200:
                    with open(save_path, 'wb') as f:
                        f.write(resp.content)
                    logger.info(f'图片下载成功: {save_name}')
                    return True
                else:
                    logger.error(f'图片下载失败: {url}, status={resp.status_code}')
                    return False
        except Exception as e:
            logger.error(f'图片下载出错: {e}')
            return False
    
    def _get_news_with_selenium(self) -> Optional[str]:
        """使用 Selenium 获取动态加载的新闻"""
        if not SELENIUM_AVAILABLE:
            logger.error('Selenium 未安装，无法获取动态新闻')
            return None
        
        driver = None
        try:
            # 配置 Chrome 选项
            chrome_options = Options()
            chrome_options.add_argument('--headless')  # 无头模式
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            # 指定chromedriver路径在./chromedriver
            chrome_options.binary_location = './chromedriver'
            
            # 启动浏览器
            driver = webdriver.Chrome(options=chrome_options)
            driver.get(IMAS_NEWS_URL)
            
            # 等待页面加载
            wait = WebDriverWait(driver, 10)
            
            # 等待按钮出现并找到 data-selected="true" 的按钮
            wait.until(EC.presence_of_element_located((By.CLASS_NAME, 'style_btn__u7_Bt')))
            
            # 如果第一个按钮不是 selected 状态，点击它
            buttons = driver.find_elements(By.CLASS_NAME, 'style_btn__u7_Bt')
            if buttons:
                first_button = buttons[0]
                if first_button.get_attribute('data-selected') != 'true':
                    first_button.click()
                    # 等待内容加载
                    import time
                    time.sleep(2)
            
            # 等待新闻卡片加载
            wait.until(EC.presence_of_element_located((By.CLASS_NAME, 'style_card__uwotf')))
            
            # 获取页面 HTML
            html = driver.page_source
            return html
            
        except Exception as e:
            logger.error(f'Selenium 获取页面失败: {e}')
            return None
        finally:
            if driver:
                driver.quit()


    
    async def _get_latest_news(self) -> list:
        """获取最新的新闻列表"""
        try:
            # 使用 Selenium 获取动态内容
            html = await asyncio.get_event_loop().run_in_executor(
                None, self._get_news_with_selenium
            )
            
            if not html:
                logger.error('获取页面 HTML 失败')
                return []
            
            soup = BeautifulSoup(html, 'lxml')
            
            # 获取所有新闻卡片
            news_items = []
            articles = soup.find_all('div', class_='style_card__uwotf')
            
            if not articles:
                logger.error('未找到新闻卡片元素')
                return []
            
            logger.info(f'找到 {len(articles)} 条新闻')
            
            for article in articles[:10]:  # 只获取最新的 10 条
                try:
                    # 提取标题和链接 - 在 style_title_link__FM_4I 类中
                    title_link = article.find('a', class_='style_title_link__FM_4I')
                    if not title_link:
                        logger.warning('未找到标题链接元素')
                        continue
                    
                    # 获取标题文本
                    title = title_link.get_text(strip=True)
                    
                    # 获取新闻链接
                    news_url = title_link.get('href', '')
                    if news_url and not news_url.startswith('http'):
                        news_url = 'https://idolmaster-official.jp' + news_url
                    
                    # 使用 URL 作为唯一 ID
                    news_id = news_url
                    
                    # 提取日期（通常在卡片上方）
                    date_elem = article.find('time') or article.find('p', class_=lambda x: x and 'date' in x.lower())
                    date = date_elem.get_text(strip=True) if date_elem else ''
                    
                    # 提取图片 - 在 style_thumb_link__emQuk 类中
                    img_link = article.find('a', class_='style_thumb_link__emQuk')
                    img_url = ''
                    if img_link:
                        img_elem = img_link.find('img')
                        if img_elem:
                            img_url = img_elem.get('src') or img_elem.get('data-src', '')
                            if img_url and not img_url.startswith('http'):
                                if img_url.startswith('//'):
                                    img_url = 'https:' + img_url
                                else:
                                    img_url = 'https://idolmaster-official.jp' + img_url
                    
                    news_items.append({
                        'id': news_id,
                        'title': title,
                        'date': date,
                        'url': news_url,
                        'img_url': img_url
                    })
                    
                    logger.debug(f'解析新闻: {title[:20]}...')
                    
                except Exception as e:
                    logger.error(f'解析新闻项失败: {e}')
                    continue
            
            return news_items
            
        except Exception as e:
            logger.error(f'获取新闻失败: {e}')
            return []
    
    async def _check_update(self) -> list:
        """检查是否有新闻更新"""
        news_list = await self._get_latest_news()
        if not news_list:
            return []
        
        # 筛选出新的新闻
        new_items = []
        for news in news_list:
            if news['id'] not in self.idx_cache:
                new_items.append(news)
        
        # 更新缓存
        if new_items:
            self.idx_cache.update(item['id'] for item in news_list)
            self.item_cache = news_list[:5]  # 只保存最新 5 条
            self._save_cache()
        
        return new_items
    
    async def _format_news(self, news_item: dict) -> list:
        """格式化新闻为消息链"""
        message_chain = []
        
        # 添加日期（如果有）
        if news_item.get('date'):
            message_chain.append(Comp.Plain(text=f"【{news_item['date']}】\n"))
        
        # 添加标题
        message_chain.append(Comp.Plain(text=f"{news_item['title']}\n"))
        
        # 下载并添加图片
        if news_item.get('img_url'):
            # 生成保存文件名
            img_name = f"{abs(hash(news_item['id']))}.jpg"
            img_path = os.path.join(self.img_dir, img_name)
            
            success = await self._download_image(news_item['img_url'], img_name)
            if success:
                # 使用本地路径构造图片消息
                message_chain.append(Comp.Image(file=img_path))
                message_chain.append(Comp.Plain(text="\n"))
        
        # 添加链接
        message_chain.append(Comp.Plain(text=f"▲{news_item['url']}"))
        
        return message_chain
    
    async def _check_news_update(self):
        """定时检查偶像大师官网新闻更新（定时任务回调）"""
        try:
            if not self.idx_cache:
                # 首次运行，加载缓存后全部推送一次
                await self._check_update()
                logger.info('IM@S 新闻缓存为空，已加载至最新')
                return
            
            new_items = await self._check_update()
            
            if not new_items:
                logger.info('未检索到 IM@S 新闻更新')
                return
            
            logger.info(f'检索到 {len(new_items)} 条 IM@S 新闻更新！')
            
            # 格式化并推送新闻（按时间顺序推送）
            for item in reversed(new_items):
                message_chain = await self._format_news(item)
                
                # 这里需要实现广播逻辑
                # 由于 AstrBot 没有直接的广播功能，需要存储订阅的群组
                # 暂时先记录日志
                logger.info(f'新闻推送: {item["title"]}')
                # TODO: 实现群组广播功能
            
            # 发送后执行图片资源清理
            await self._cleanup_images()
                
        except Exception as e:
            logger.error(f'检查新闻更新失败: {e}')
    
    async def _cleanup_images(self):
        """清理旧的图片资源"""
        try:
            existing_files = set(os.listdir(self.img_dir))
            cached_files = {f"{abs(hash(item['id']))}.jpg" for item in self.item_cache}
            to_delete = existing_files - cached_files
            
            for filename in to_delete:
                try:
                    os.remove(os.path.join(self.img_dir, filename))
                    logger.info(f'已删除旧图片资源: {filename}')
                except Exception as e:
                    logger.error(f'删除图片资源失败: {e}')
                finally:
                    await asyncio.sleep(0.1)  # 避免阻塞
        except Exception as e:
            logger.error(f'清理图片资源失败: {e}')
    
    # ============ 指令处理器 ============
    
    @filter.command("imas新闻", alias={'im@s新闻', '偶像大师新闻'})
    async def send_imas_news(self, event: AstrMessageEvent):
        """手动获取最新新闻"""
        if not self.item_cache:
            await self._check_update()
        
        if not self.item_cache:
            yield event.plain_result('暂无 IM@S 新闻')
            return
        
        # 发送最新 3 条新闻
        for item in self.item_cache[:3]:
            message_chain = await self._format_news(item)
            yield event.chain_result(message_chain)
            await asyncio.sleep(0.5)  # 避免发送过快
