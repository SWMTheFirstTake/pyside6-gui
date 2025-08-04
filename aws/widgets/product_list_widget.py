#!/usr/bin/env python3
"""
상품 목록 위젯
DynamoDB에서 상품 데이터를 페이지네이션으로 로드하고 표시합니다.
"""

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget, 
                               QListWidgetItem, QLabel, QPushButton, QComboBox,
                               QProgressBar, QMessageBox, QFrame, QSpacerItem,
                               QSizePolicy)
from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QFont, QPalette
from typing import Optional, Dict, Any, List
import logging

from aws_manager import AWSManager
logger = logging.getLogger(__name__)


class ProductLoadThread(QThread):
    """
    상품 로드 스레드(dynamoDB에서 상품 데이터를 페이지네이션으로 로드)
    """
    
    products_loaded = Signal(list, dict)  # dynamoDB에서 조회한 상품 데이터, 마지막 키 정보
    error_occurred = Signal(str)
    
    def __init__(self, aws_manager, sub_category: int = None, status_filter: str = None,
                 exclusive_start_key: Dict[str, Any] = None, limit: int = 20):
        super().__init__()
        self.aws_manager:AWSManager = aws_manager
        self.sub_category = sub_category
        self.status_filter = status_filter
        self.exclusive_start_key = exclusive_start_key
        self.limit = limit
    
    def run(self):
        """스레드 실행"""
        try:
           
            products, last_key = self.aws_manager.get_product_by_status(
                    status=self.status_filter,
                    limit=self.limit,
                    exclusive_start_key=self.exclusive_start_key,
                    sub_category=self.sub_category
            )
           
            
            # dynamoDB에서 조회한 데이터를 Signal로 방출 => self.on_products_loaded() 함수에게 전달 
            self.products_loaded.emit(products, last_key or {})
            
        except Exception as e:
            logger.error(f"상품 로드 중 오류: {e}")
            self.error_occurred.emit(str(e))


class PageImagePreloadThread(QThread):
    """페이지별 이미지 정보 수집 스레드"""
    
    preload_ready = Signal(list)  # download_tasks
    error_occurred = Signal(str)
    
    def __init__(self, aws_manager, main_category: str, sub_category: int, product_data_list: List[Dict[str, Any]]):
        super().__init__()
        self.aws_manager = aws_manager
        self.main_category = main_category
        self.sub_category = sub_category
        self.product_data_list = product_data_list
    
    def run(self):
        """스레드 실행"""
        try:
            # 이미 가져온 제품 데이터에서 직접 이미지 정보 수집 (DynamoDB 추가 호출 없음)
            download_tasks = self.aws_manager.batch_get_product_images_from_data(
                self.main_category, self.sub_category, self.product_data_list
            )
            self.preload_ready.emit(download_tasks)
            
        except Exception as e:
            logger.error(f"이미지 정보 수집 중 오류: {e}")
            self.error_occurred.emit(str(e))


class ProductItem(QWidget):
    """상품 아이템 위젯(각 제품 id에 대한 정보를 표시 클래스)"""
    
    def __init__(self, product_data: Dict[str, Any]):
        super().__init__()
        self.product_data = product_data
        self.setup_ui()
    
    def setup_ui(self):
        """UI 설정"""
        # 전체 위젯에 카드 스타일 적용
        self.setStyleSheet("""
            QWidget {
                background-color: white;
                border: 1px solid #dee2e6;
                border-radius: 8px;
                margin: 4px;
            }
            QWidget:hover {
                border-color: #007bff;
                background-color: #f0f8ff;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        
        # 상품 ID
        product_id = self.product_data.get('product_id', 'Unknown')
        id_label = QLabel(f"ID: {product_id}")
        id_font = QFont()
        id_font.setBold(True)
        id_font.setPointSize(10)
        id_label.setFont(id_font)
        id_label.setStyleSheet("color: #212529; background-color: transparent; padding: 2px;")
        layout.addWidget(id_label)
        
        # 상품 정보(dynamoDB의 product_info 필드에 있는 데이터를 표시)
        product_info = self.product_data.get('product_info', {})
        if isinstance(product_info, dict):
            brand = product_info.get('brand', 'Unknown')
            product_name = product_info.get('product_name', 'Unknown')
            info_text = f"{brand} - {product_name}"
        else:
            info_text = "상품 정보 없음"
        
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #495057; background-color: #f8f9fa; padding: 6px; border-radius: 4px; border: 1px solid #e9ecef;")
        layout.addWidget(info_label)
        
        # 상태 정보
        status_layout = QHBoxLayout()
        
        # 현재 상태
        current_status = self.product_data.get('current_status', 'PENDING')
        status_label = QLabel(f"상태: {current_status}")
        
        if current_status == 'COMPLETED':
            status_label.setStyleSheet("color: #155724; background-color: #d4edda; font-weight: bold; padding: 4px 8px; border-radius: 4px; border: 1px solid #c3e6cb;")
        elif current_status == 'PASS':
            status_label.setStyleSheet("color: #856404; background-color: #fff3cd; font-weight: bold; padding: 4px 8px; border-radius: 4px; border: 1px solid #ffeaa7;")
        elif current_status == 'IN_PROGRESS':
            status_label.setStyleSheet("color: #856404; background-color: #fff3cd; font-weight: bold; padding: 4px 8px; border-radius: 4px; border: 1px solid #ffeaa7;")
        else:
            status_label.setStyleSheet("color: #721c24; background-color: #f8d7da; font-weight: bold; padding: 4px 8px; border-radius: 4px; border: 1px solid #f5c6cb;")
        
        status_layout.addWidget(status_label)
        
        # 색상 개수
        available_colors = self.product_data.get('available_colors', [])
        if available_colors:
            color_count = len(available_colors)
            color_label = QLabel(f"색상: {color_count}개")
            color_label.setStyleSheet("color: #0056b3; background-color: #e7f1ff; padding: 3px 8px; border-radius: 4px; font-weight: bold; border: 1px solid #b3d7ff;")
            status_layout.addWidget(color_label)
        
        status_layout.addStretch()
        layout.addLayout(status_layout)
        
        # 마지막 업데이트 시간
        last_updated = self.product_data.get('last_updated_at', '')
        if last_updated:
            time_label = QLabel(f"업데이트: {last_updated[:10]}")  # 날짜만 표시
            time_label.setStyleSheet("color: #6c757d; background-color: #f8f9fa; font-size: 9px; padding: 3px 6px; border-radius: 3px; border: 1px solid #e9ecef;")
            layout.addWidget(time_label)
    
    def get_product_id(self) -> str:
        """상품 ID 반환"""
        return self.product_data.get('product_id', '')
    
    def get_product_data(self) -> Dict[str, Any]:
        """상품 데이터 반환"""
        return self.product_data
    
    def update_status(self, new_status: str):
        """상태 업데이트 - 상태 레이블만 업데이트하여 레이아웃 충돌 방지"""
        self.product_data['current_status'] = new_status
        
        # 기존 위젯들을 찾아서 상태 레이블만 업데이트
        try:
            # 레이아웃을 완전히 재생성하지 않고 상태 레이블만 찾아서 업데이트
            self._update_status_label_only(new_status)
        except Exception as e:
            logger.error(f"상태 레이블 업데이트 실패: {e}")
            # 폴백: 전체 위젯 재생성 (더 안전한 방식으로)
            self._recreate_widget_safely(new_status)
    
    def _update_status_label_only(self, new_status: str):
        """상태 레이블만 업데이트하는 안전한 방법"""
        # 현재 레이아웃에서 상태 관련 위젯들을 찾아서 업데이트
        layout = self.layout()
        if not layout:
            return
            
        # 레이아웃을 순회하면서 상태 레이블 찾기
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item.layout():  # 중첩된 레이아웃인 경우
                nested_layout = item.layout()
                for j in range(nested_layout.count()):
                    nested_item = nested_layout.itemAt(j)
                    if nested_item.widget():
                        widget = nested_item.widget()
                        if isinstance(widget, QLabel) and widget.text().startswith("상태:"):
                            # 상태 레이블 업데이트
                            widget.setText(f"상태: {new_status}")
                            
                            # 상태에 따른 스타일 업데이트
                            if new_status == 'COMPLETED':
                                widget.setStyleSheet("color: #155724; background-color: #d4edda; font-weight: bold; padding: 4px 8px; border-radius: 4px; border: 1px solid #c3e6cb;")
                            elif new_status == 'PASS':
                                widget.setStyleSheet("color: #856404; background-color: #fff3cd; font-weight: bold; padding: 4px 8px; border-radius: 4px; border: 1px solid #ffeaa7;")
                            elif new_status == 'IN_PROGRESS':
                                widget.setStyleSheet("color: #856404; background-color: #fff3cd; font-weight: bold; padding: 4px 8px; border-radius: 4px; border: 1px solid #ffeaa7;")
                            else:
                                widget.setStyleSheet("color: #721c24; background-color: #f8d7da; font-weight: bold; padding: 4px 8px; border-radius: 4px; border: 1px solid #f5c6cb;")
                            return
    
    def _recreate_widget_safely(self, new_status: str):
        """안전한 방식으로 위젯 재생성"""
        # 부모 위젯과 사이즈 정보 저장
        parent_widget = self.parent()
        current_size = self.size()
        
        # 새로운 ProductItem 생성
        new_widget = ProductItem(self.product_data)
        new_widget.resize(current_size)
        
        # 현재 위젯을 새로운 위젯으로 교체하는 로직은 복잡하므로
        # 현재는 상태 데이터만 업데이트하고 다음 새로고침 시 반영되도록 함
        logger.info(f"상품 {self.product_data.get('product_id')} 상태가 {new_status}로 업데이트됨 (다음 새로고침 시 UI 반영)")


class ProductListWidget(QWidget):
    """
    상품 목록 위젯(ProductItem 클래스를 사용하여 상품 목록을 표시)
    - product_selected : dynamoDB에서 조회한 상품 데이터정보가 QListWidgetItem에 저장된 상태에서 특정 제품 클릭시 해당 data가 딕셔너리로 형태로 방출 
                         => MainWindow 클래스에서 정의한 (self.on_product_selected) 함수에게 딕셔너리로 전달
    - page_changed : 페이지 변경 시 상품 목록 로드
    - page_images_preloaded : 페이지 이미지 프리로딩 완료 시그널
    """
    
    product_selected = Signal(dict)
    page_changed = Signal(int)  # 페이지 변경
    page_images_preloaded = Signal()  # 페이지 이미지 프리로딩 완료
    
    def __init__(self):
        super().__init__()
        self.aws_manager = None
        self.image_cache = None
        self.current_products = []
        self.current_page = 0
        self.last_evaluated_key = None
        self.total_pages = 0
        self.load_thread = None
        self.preload_thread = None
        self.background_color = "#bcbcbc"
        self.current_sub_category = None
        self.current_main_category = None
        self.completed_product_ids = set()  # 완료된 제품 ID들을 추적
        
        self.setup_ui()
    
    def setup_ui(self):
        """UI 설정"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # 헤더
        header_frame = QFrame()
        header_frame.setStyleSheet(f"background-color: #e9ecef; color: #212529; border-bottom: 1px solid #dee2e6;")
        header_layout = QVBoxLayout(header_frame)
        header_layout.setContentsMargins(15, 10, 15, 10)
        
        # 제목
        title_label = QLabel("상품 목록")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(14)
        title_label.setFont(title_font)
        header_layout.addWidget(title_label)
        
        # 필터 컨트롤
        filter_layout = QHBoxLayout()
        
        filter_layout.addWidget(QLabel("상태:"))
        self.status_combo = QComboBox()
        self.status_combo.addItems(["ALL", "PENDING", "IN_PROGRESS", "COMPLETED", "PASS"])
        self.status_combo.currentTextChanged.connect(self.on_filter_changed)
        filter_layout.addWidget(self.status_combo)
        
        filter_layout.addStretch()
        
        # 새로고침 버튼
        self.refresh_btn = QPushButton("새로고침")
        self.refresh_btn.clicked.connect(self.refresh_products)
        filter_layout.addWidget(self.refresh_btn)
        
        header_layout.addLayout(filter_layout)
        layout.addWidget(header_frame)
        
        # 상품 목록
        self.product_list = QListWidget()
        self.product_list.setSelectionMode(QListWidget.SingleSelection)
        self.product_list.itemClicked.connect(self.on_item_clicked)
        
        # 리스트 위젯 스타일 개선
        self.product_list.setStyleSheet("""
            QListWidget {
                background-color: #f8f9fa;
                border: none;
                outline: none;
                padding: 4px;
            }
            QListWidget::item {
                border: none;
                padding: 4px;
                margin: 2px 0px;
                background-color: transparent;
                border-radius: 8px;
            }
            QListWidget::item:selected {
                background-color: #e3f2fd;
                border: 2px solid #2196f3;
                border-radius: 10px;
            }
            QListWidget::item:hover {
                background-color: #f0f8ff;
                border-radius: 8px;
            }
        """)
        
        layout.addWidget(self.product_list)
        
        # 로딩 프로그레스바
        self.loading_progress = QProgressBar()
        self.loading_progress.setVisible(False)
        self.loading_progress.setFormat("상품 목록 로딩 중... %p%")
        layout.addWidget(self.loading_progress)
        
        # 이미지 프리로딩 프로그레스바
        self.preload_progress = QProgressBar()
        self.preload_progress.setVisible(False)
        self.preload_progress.setFormat("이미지 다운로드 중... %v/%m")
        layout.addWidget(self.preload_progress)
        
        # 페이지네이션 컨트롤
        pagination_frame = QFrame()
        pagination_frame.setStyleSheet(f"background-color: #e9ecef; color: #212529; border-top: 1px solid #dee2e6;")
        pagination_layout = QHBoxLayout(pagination_frame)
        pagination_layout.setContentsMargins(15, 10, 15, 10)
        
        self.prev_btn = QPushButton("이전")
        self.prev_btn.setEnabled(False)
        self.prev_btn.clicked.connect(self.go_previous_page)
        pagination_layout.addWidget(self.prev_btn)
        
        self.page_label = QLabel("페이지 1")
        pagination_layout.addWidget(self.page_label)
        
        pagination_layout.addStretch()
        
        self.next_btn = QPushButton("다음")
        self.next_btn.setEnabled(False)
        self.next_btn.clicked.connect(self.go_next_page)
        pagination_layout.addWidget(self.next_btn)
        
        layout.addWidget(pagination_frame)
    
    def set_aws_manager(self, aws_manager):
        """AWS 매니저 설정"""
        self.aws_manager = aws_manager
    
    def set_image_cache(self, image_cache):
        """이미지 캐시 설정"""
        self.image_cache = image_cache
    
    def set_category_info(self, main_category: str, sub_category: int):
        """카테고리 정보 설정"""
        self.current_main_category = main_category
        self.current_sub_category = sub_category
    
    def set_status_filter(self, status: str):
        """상태 필터 설정"""
        try:
            # 유효한 상태인지 확인
            valid_statuses = ["ALL", "PENDING", "COMPLETED", "PASS"]
            if status in valid_statuses:
                # 시그널 차단하여 on_filter_changed가 호출되지 않도록 함
                self.status_combo.blockSignals(True)
                self.status_combo.setCurrentText(status)
                self.status_combo.blockSignals(False)
                logger.info(f"상태 필터를 {status}로 설정했습니다.")
            else:
                logger.warning(f"유효하지 않은 상태 필터: {status}")
        except Exception as e:
            logger.error(f"상태 필터 설정 오류: {e}")
    
    def on_filter_changed(self):
        """필터 변경 시"""
        self.refresh_products()
    
    def refresh_products(self):
        """상품 목록 새로고침"""
        self.current_page = 0
        self.last_evaluated_key = None
        self.load_products_async(sub_category=self.current_sub_category)

    def load_products_async(self, sub_category: int = None, exclusive_start_key: Dict[str, Any] = None):
        """비동기로 상품 로드"""
        if not self.aws_manager:
            return
        
        # sub_category가 없으면 기본값 사용
        if sub_category is None:
            sub_category = self.current_sub_category 
        
        # 현재 서브 카테고리 업데이트
        self.current_sub_category = sub_category
        
        # 이전 스레드 정리
        if self.load_thread and self.load_thread.isRunning():
            self.load_thread.quit()
            self.load_thread.wait()
        
        if self.preload_thread and self.preload_thread.isRunning():
            self.preload_thread.quit()
            self.preload_thread.wait()
        
        # 로딩 UI 표시
        self.loading_progress.setVisible(True)
        self.loading_progress.setRange(0, 0)
        self.preload_progress.setVisible(False)
        self.refresh_btn.setEnabled(False)
        
        # 스레드 생성 및 실행
        status_filter = self.status_combo.currentText()
        self.load_thread = ProductLoadThread(
            self.aws_manager,
            sub_category=sub_category,
            status_filter=status_filter,
            exclusive_start_key=exclusive_start_key,
            limit=20
        )
        
        self.load_thread.products_loaded.connect(self.on_products_loaded)
        self.load_thread.error_occurred.connect(self.on_load_error)
        self.load_thread.start()
    
    def on_products_loaded(self, products: List[Dict[str, Any]], last_evaluated_key: Dict[str, Any]):
        """상품 목록 로드 완료 처리"""
        self.current_products = products
        self.last_evaluated_key = last_evaluated_key
        
        # UI 업데이트
        self.update_product_list()
        self.update_pagination_controls()
        
        # 로딩 UI 숨김
        self.loading_progress.setVisible(False)
        self.refresh_btn.setEnabled(True)
        
        # 페이지 이미지 프리로딩 시작
        self.start_page_image_preload()
    
    def start_page_image_preload(self):
        """페이지 이미지 프리로딩 시작"""
        if not self.image_cache or not self.current_main_category or not self.current_products:
            return
        
        try:
            # 현재 페이지 제품 ID 목록 생성 (캐시 설정용)
            product_ids = [product.get('product_id') for product in self.current_products if product.get('product_id')]
            
            if not product_ids:
                return
            
            # 캐시에 현재 페이지 제품 설정
            self.image_cache.set_current_page_products(product_ids)
            
            # 현재 페이지의 제품들 중 완료된 제품들을 추적에 추가
            for product in self.current_products:
                product_id = product.get('product_id')
                if product_id and product.get('current_status') == 'COMPLETED':
                    self.completed_product_ids.add(product_id)
            
            # 완료된 제품들의 캐시 정리 (현재 페이지가 아닌 것들만)
            completed_ids_to_remove = [pid for pid in self.completed_product_ids if pid not in product_ids]
            if completed_ids_to_remove:
                self.image_cache.clear_non_current_page_cache(completed_ids_to_remove)
                # 정리된 제품들을 추적에서 제거
                for pid in completed_ids_to_remove:
                    self.completed_product_ids.discard(pid)
            
            # 캐시에 이미지가 이미 있는지 확인
            cached_count = 0
            missing_products = []
            
            for product in self.current_products:
                product_id = product.get('product_id')
                if not product_id:
                    continue
                    
                # 캐시에 해당 제품의 이미지가 있는지 확인
                cached_images = self.image_cache.get_product_images(product_id)
                if cached_images and any(images for images in cached_images.values()):
                    cached_count += 1
                    logger.debug(f"제품 {product_id} 이미지가 캐시에 이미 존재")
                else:
                    missing_products.append(product)
                    logger.debug(f"제품 {product_id} 이미지가 캐시에 없음")
            
            # 모든 제품의 이미지가 캐시에 있는 경우 다운로드 건너뛰기
            if cached_count == len(self.current_products):
                logger.info(f"모든 제품 이미지가 캐시에 존재 ({cached_count}개), 다운로드 건너뛰기")
                self.page_images_preloaded.emit()
                return
            
            # 일부 제품의 이미지가 없는 경우, 해당 제품들만 다운로드
            logger.info(f"캐시된 제품: {cached_count}개, 다운로드 필요: {len(missing_products)}개")
            
            if not missing_products:
                # 다운로드할 제품이 없으면 바로 완료
                self.page_images_preloaded.emit()
                return
            
            # 이미지 정보 수집 스레드 시작 (캐시에 없는 제품들만)
            self.preload_thread = PageImagePreloadThread(
                self.aws_manager, 
                self.current_main_category, 
                self.current_sub_category, 
                missing_products  # 캐시에 없는 제품들만 전달
            )
            
            self.preload_thread.preload_ready.connect(self.on_preload_ready)
            self.preload_thread.error_occurred.connect(self.on_preload_error)
            self.preload_thread.start()
            
            # 프리로딩 프로그레스바 표시
            self.preload_progress.setVisible(True)
            self.preload_progress.setRange(0, 0)  # 무한 진행바
            self.preload_progress.setFormat("이미지 정보 수집 중...")
            
        except Exception as e:
            logger.error(f"페이지 이미지 프리로딩 시작 실패: {e}")
    
    def on_preload_ready(self, download_tasks: List[Dict]):
        """이미지 정보 수집 완료, 다운로드 시작"""
        if not download_tasks:
            self.preload_progress.setVisible(False)
            self.page_images_preloaded.emit()
            return
        
        try:
            # 다운로드 프로그레스바 설정
            self.preload_progress.setRange(0, len(download_tasks))
            self.preload_progress.setValue(0)
            self.preload_progress.setFormat(f"이미지 다운로드 중... 0/{len(download_tasks)}")
            
            # 이미지 캐시로 배치 다운로드 시작
            success = self.image_cache.download_page_images(
                download_tasks,
                progress_callback=self.on_download_progress,
                completed_callback=self.on_download_completed
            )
            
            if not success:
                self.preload_progress.setVisible(False)
                logger.error("이미지 다운로드 시작 실패")
                
        except Exception as e:
            logger.error(f"이미지 다운로드 시작 중 오류: {e}")
            self.preload_progress.setVisible(False)
    
    def on_download_progress(self, current: int, total: int):
        """다운로드 진행률 업데이트"""
        self.preload_progress.setValue(current)
        self.preload_progress.setFormat(f"이미지 다운로드 중... {current}/{total}")
    
    def on_download_completed(self):
        """이미지 다운로드 완료"""
        self.preload_progress.setVisible(False)
        self.page_images_preloaded.emit()
        logger.info(f"페이지 이미지 프리로딩 완료: {len(self.current_products)}개 제품")
    
    def on_preload_error(self, error_message: str):
        """이미지 프리로딩 오류"""
        self.preload_progress.setVisible(False)
        logger.error(f"이미지 프리로딩 오류: {error_message}")
    
    def on_load_error(self, error_message: str):
        """로드 오류 처리"""
        self.loading_progress.setVisible(False)
        self.preload_progress.setVisible(False)
        self.refresh_btn.setEnabled(True)
        
        QMessageBox.warning(self, "로드 오류", f"상품 목록을 불러오는 중 오류가 발생했습니다:\n{error_message}")
    
    def update_product_list(self):
        """상품 목록 UI 업데이트"""
        self.product_list.clear()
        
        for product in self.current_products:
            # 아이템 위젯 생성
            item_widget = ProductItem(product)
            
            # 리스트 아이템 생성
            list_item = QListWidgetItem()
            
            # 아이템 크기 설정 (여백 포함)
            widget_size = item_widget.sizeHint()
            list_item.setSizeHint(QSize(widget_size.width(), widget_size.height() + 8))
            
            # 아이템에 데이터 저장
            list_item.setData(Qt.UserRole, product)
            
            # 리스트에 추가
            self.product_list.addItem(list_item)
            self.product_list.setItemWidget(list_item, item_widget)
    
    def update_pagination_controls(self):
        """페이지네이션 컨트롤 업데이트"""
        self.page_label.setText(f"페이지 {self.current_page + 1}")
        
        # 이전 버튼
        self.prev_btn.setEnabled(self.current_page > 0)
        
        # 다음 버튼 (마지막 키가 있으면 다음 페이지 존재)
        self.next_btn.setEnabled(bool(self.last_evaluated_key))
    
    def go_previous_page(self):
        """이전 페이지로 이동"""
        if self.current_page > 0:
            self.current_page -= 1
            self.page_changed.emit(self.current_page)
    
    def go_next_page(self):
        """다음 페이지로 이동"""
        if self.last_evaluated_key:
            self.current_page += 1
            self.load_products_async(sub_category=self.current_sub_category, exclusive_start_key=self.last_evaluated_key)
    
    def on_item_clicked(self, item: QListWidgetItem):
        """아이템 클릭 시"""
        product_data = item.data(Qt.UserRole)
        if product_data:
            # 메인 카테고리 정보 추가
            if self.current_main_category:
                product_data['main_category'] = self.current_main_category
            self.product_selected.emit(product_data)
    
    def update_product_status(self, product_id: str, new_status: str):
        """특정 상품의 상태 업데이트"""
        for i in range(self.product_list.count()):
            item = self.product_list.item(i)
            product_data = item.data(Qt.UserRole)
            
            if product_data and product_data.get('product_id') == product_id:
                # 데이터 업데이트
                product_data['current_status'] = new_status
                item.setData(Qt.UserRole, product_data)
                
                # 완료된 제품 추적
                if new_status == 'COMPLETED':
                    self.completed_product_ids.add(product_id)
                    logger.debug(f"제품 {product_id}가 완료 상태로 변경되어 추적 목록에 추가됨")
                
                # 위젯 업데이트
                item_widget = self.product_list.itemWidget(item)
                if isinstance(item_widget, ProductItem):
                    item_widget.update_status(new_status)
                break
    
    def cleanup(self):
        """정리 작업"""
        try:
            # 로드 스레드 정리
            if self.load_thread:
                if self.load_thread.isRunning():
                    self.load_thread.quit()
                    if not self.load_thread.wait(3000):  # 3초 대기
                        self.load_thread.terminate()  # 강제 종료
                        self.load_thread.wait()
                self.load_thread.deleteLater()
                self.load_thread = None
            
            # 프리로드 스레드 정리
            if self.preload_thread:
                if self.preload_thread.isRunning():
                    self.preload_thread.quit()
                    if not self.preload_thread.wait(3000):  # 3초 대기
                        self.preload_thread.terminate()  # 강제 종료
                        self.preload_thread.wait()
                self.preload_thread.deleteLater()
                self.preload_thread = None
            
            # UI 정리
            self.product_list.clear()
            self.current_products.clear()
            self.last_evaluated_key = None
            self.completed_product_ids.clear()  # 완료된 제품 ID 추적 정리
            
        except Exception as e:
            logger.error(f"ProductListWidget 정리 중 오류: {str(e)}")
    
    def keyPressEvent(self, event):
        """키보드 이벤트를 부모로 전달하여 전역 단축키가 작동하도록 함"""
        # 키보드 이벤트를 부모(MainWindow)로 전달
        if self.parent():
            self.parent().keyPressEvent(event)
        
        # 이벤트가 처리되지 않은 경우에만 기본 동작 수행
        if not event.isAccepted():
            super().keyPressEvent(event) 