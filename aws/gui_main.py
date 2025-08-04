#!/usr/bin/env python3
"""
AI 데이터셋 큐레이션 GUI 메인 애플리케이션
"""

import sys
import os
import logging
from pathlib import Path

# 현재 디렉토리를 Python 경로에 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QSplitter, QStatusBar, 
                               QProgressBar, QLabel,QMessageBox, QDialog,
                               QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QAction, QKeyEvent

from aws_manager import AWSManager
from image_cache import ProductImageCache
from widgets.main_image_viewer import MainImageViewer
from widgets.representative_panel import RepresentativePanel
from widgets.product_list_widget import ProductListWidget
from widgets.category_selection_dialog import CategorySelectionDialog

from typing import Annotated
import logging
logging.basicConfig(level=logging.INFO , format='%(asctime)s - %(name)s - %(levelname)s - %(message)s - %(filename)s - %(lineno)d')

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """메인 윈도우 클래스"""
    
    
    def __init__(self):
        super().__init__()
        self.aws_manager:AWSManager |None = None
        self.representative_panel: RepresentativePanel |None = None
        self.main_image_viewer: MainImageViewer |None = None
        self.product_list_widget: ProductListWidget |None = None
        self.category_selection_dialog: CategorySelectionDialog |None = None
        
        
        self.image_cache = ProductImageCache()  # 새로운 ProductImageCache 사용
        self.current_page = 0
        self.last_evaluated_key = None
        self.selected_main_category = None
        self.selected_sub_category = None
        
        # 상태 통계 관리 (단순화)
        self.current_stats = {'pending': 0, 'completed': 0, 'pass': 0, 'total': 0}
        
        self.setup_ui()
        self.setup_connections()
        self.initialize_aws()
    
    def setup_ui(self):
        """
        UI 구성 요소들을 설정합니다.
        스플리터를 사용하여 상품 목록, 메인 이미지 뷰어, 대표 이미지 패널을 배치합니다.
        메뉴바, 중앙 위젯, 상태바를 포함한 전체 UI 레이아웃을 구성합니다.
        
        레이아웃 구조:
        - 왼쪽: ProductListWidget (상품 목록) -> product_list_widget.py 
        - 중앙: MainImageViewer (이미지 뷰어, 가장 큰 영역) -> main_image_viewer.py 
        - 오른쪽: RepresentativePanel (대표 이미지 선정) -> representative_panel.py 
        """
        self.setWindowTitle("AI 데이터셋 큐레이션 도구")
        
        # 화면의 사용 가능한 크기 가져오기
        available_geometry = QApplication.primaryScreen().availableGeometry()
        
        # 초기 윈도우 크기를 화면 크기의 80%로 설정
        width = int(available_geometry.width() * 0.8)
        height = int(available_geometry.height() * 0.8)
        x = (available_geometry.width() - width) // 2
        y = (available_geometry.height() - height) // 2
        
        # 최소 크기 설정
        self.setMinimumSize(1200, 800)
        
        # 초기 위치와 크기 설정
        self.setGeometry(x, y, width, height)
        
        # 메뉴바 설정(앱 상단 메뉴바 설정)
        self.setup_menu_bar()
        
        # 중앙 위젯 설정
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 메인 레이아웃
        main_layout = QHBoxLayout(central_widget)
        
        # 스플리터 생성
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)
        
        # 왼쪽 패널: 상품 목록(dynamoDB에서 조회한 상품 목록을 표시)
        self.product_list_widget = ProductListWidget()
        splitter.addWidget(self.product_list_widget)
        
        # 중앙 패널: 메인 이미지 뷰어
        self.main_image_viewer = MainImageViewer()
        splitter.addWidget(self.main_image_viewer)
        
        # 오른쪽 패널: 대표 이미지 선정 패널
        self.representative_panel = RepresentativePanel()
        splitter.addWidget(self.representative_panel)
        
        # 패널 간 참조 설정
        self.main_image_viewer.set_representative_panel(self.representative_panel)
        # RepresentativePanel에 MainImageViewer 참조 설정
        self.representative_panel.set_main_image_viewer(self.main_image_viewer)
        
        # 스플리터 비율 설정 - (왼쪽 , 중앙 , 오른쪽 패널 width 비율)
        total_width = self.width()
        splitter.setSizes([
            int(total_width * 0.1),  # 왼쪽 패널 20%
            int(total_width * 0.6),  # 중앙 패널 60%
            int(total_width * 0.3)   # 오른쪽 패널 20%
        ])
        
        
        # 상태바 설정
        self.setup_status_bar()
        
        # 최대화 상태로 시작
        self.showMaximized()
    
    def setup_menu_bar(self):
        """
        메뉴바와 액션들을 설정합니다.
        파일 메뉴와 도구 메뉴를 포함하며, 각각 단축키도 설정합니다.
        
        메뉴 구성:
        - 파일 메뉴: 카테고리 변경(Ctrl+C), 새로고침(F5), 종료(Ctrl+Q)
        - 도구 메뉴: 통계 보기, 캐시 정리
        """
        menubar = self.menuBar()
        
        # 파일 메뉴
        file_menu = menubar.addMenu('파일(&F)')
        
        # 카테고리 변경 액션(상단 메뉴 바에서 카테고리 변경 누른경우)
        change_category_action = QAction('카테고리 변경(&C)', self)
        change_category_action.setShortcut('Ctrl+C')
        change_category_action.triggered.connect(self.show_category_selection)
        file_menu.addAction(change_category_action)
        
        file_menu.addSeparator()
        
        # 새로고침 액션
        refresh_action = QAction('새로고침(&R)', self)
        refresh_action.setShortcut('F5')
        refresh_action.triggered.connect(self.refresh_data)
        file_menu.addAction(refresh_action)
        
        file_menu.addSeparator()
        
        # 종료 액션
        exit_action = QAction('종료(&X)', self)
        exit_action.setShortcut('Ctrl+Q')
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # 도구 메뉴
        tools_menu = menubar.addMenu('도구(&T)')
        
        # 통계 보기 액션
        stats_action = QAction('통계 보기(&S)', self)
        stats_action.triggered.connect(self.show_statistics)
        tools_menu.addAction(stats_action)
        
        # 캐시 정리 액션
        clear_cache_action = QAction('캐시 정리(&C)', self)
        clear_cache_action.triggered.connect(self.clear_cache)
        tools_menu.addAction(clear_cache_action)
    
    def setup_status_bar(self):
        """
        상태바와 그 구성 요소들을 설정합니다. 연결 상태, 카테고리 정보, 진행률 표시기, 작업 정보를 포함합니다.
        상태바 구성 요소:
        - connection_label: AWS 연결 상태 표시
        - category_label: 현재 선택된 카테고리 정보
        - progress_bar: 작업 진행률 (필요시에만 표시)
        - work_info_label: 현재 작업 정보
        """
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        # 연결 상태 레이블
        self.connection_label = QLabel("연결 상태: 확인 중...")
        self.connection_label.setStyleSheet("color: #28a745; background-color: transparent; font-weight: bold;")
        self.status_bar.addWidget(self.connection_label)
        
        # 카테고리 정보 레이블
        self.category_label = QLabel("카테고리: 선택되지 않음")
        self.category_label.setStyleSheet("color: #495057; background-color: transparent; padding-left: 20px;")
        self.status_bar.addWidget(self.category_label)
        
        # 상태 통계 레이블
        self.stats_label = QLabel("통계: 대기 중")
        self.stats_label.setStyleSheet("color: #495057; background-color: transparent; padding-left: 20px;")
        self.status_bar.addWidget(self.stats_label)
        
        # 진행률 표시
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)
        
        # 작업 정보 레이블
        self.work_info_label = QLabel("준비 완료")
        self.status_bar.addPermanentWidget(self.work_info_label)
    
    #RECHECK : 중요 함수(시그널-슬롯 연결 설정) 
    def setup_connections(self):
        """
        시그널-슬롯 연결을 설정합니다.
        상품 선택, 대표 이미지 선정, 큐레이션 완료, 페이지 변경 등의 이벤트를 연결합니다.
        
        연결되는 시그널-슬롯:
        - product_list_widget.product_selected → on_product_selected: 상품 선택 시
        - representative_panel.curation_completed → on_curation_completed: 큐레이션 완료 시
        - product_list_widget.page_changed → load_products_page: 페이지 변경 시
        - product_list_widget.page_images_preloaded → on_page_images_preloaded: 페이지 이미지 프리로딩 완료 시
        """
        # 상품 선택 시 해당 상품의 데이터 방출 및 전달하는(시그널-슬롯 연결)
        self.product_list_widget.product_selected.connect(self.on_product_selected)
        
        # 메인 이미지 뷰어의 Signal(representative_selected) 시그널이 방출되면 우측 패널의 메서드(add_representative_image) 동작 , (대표이미지(dict) , 타입(str)) 전달 
        self.main_image_viewer.representative_selected.connect(
            self.representative_panel.add_representative_image)
        
        # 큐레이션 완료 시(우측 패널에서의 큐레이션 완료 버튼이 눌리면, 완료된 상품 id 전달)
        self.representative_panel.curation_completed.connect(self.on_curation_completed)
        
        # 상품 보류 처리 시
        self.representative_panel.product_passed.connect(self.on_product_passed)
        
        # 페이지 변경 시
        self.product_list_widget.page_changed.connect(self.load_products_page)
        
        # 페이지 이미지 프리로딩 완료 시
        self.product_list_widget.page_images_preloaded.connect(self.on_page_images_preloaded)

    def on_product_selected(self, product_data: dict):
        """
        args:
            product_data(dict) : dynamoDB에서 조회한 상품 개별 딕셔너리 정보(좌측 패널 위젯에서 특정 상품 클릭시 데이터 전달받음) \n
                                 ProductListWidget 클래스에서 정의한 커스텀 Signal이 전송하는 데이터 
        """
        # 대표 이미지 패널에 상품 정보 로드
        self.representative_panel.load_product(product_data)
        
        # 로컬 캐시에서 이미지를 로드하고 메인 이미지 뷰어에 전달
        self.load_product_images_from_cache(product_data)
    
    def load_product_images_from_cache(self, product_data):
        """
        로컬 캐시에서 제품 이미지를 로드하여 메인 이미지 뷰어에 전달
        
        args:
            product_data(dict) : dynamoDB에서 조회한 상품 개별 딕셔너리 정보
        """
        try:
            product_id = product_data['product_id']
            
            logger.info(f"캐시에서 이미지 로드 시작: {product_id}")
            
            # 캐시에서 제품 이미지 정보 조회
            cached_images = self.image_cache.get_product_images(product_id)
            
            if not cached_images:
                logger.warning(f"캐시에 이미지가 없습니다: {product_id}")
                # 빈 이미지로 메인 뷰어 초기화
                self.main_image_viewer.load_product_images([], product_data)
                return
            
            # 캐시된 이미지 정보를 메인 이미지 뷰어 형식으로 변환
            all_images = []
            for folder, images in cached_images.items():
                for image_info in images:
                    # file:// URL 형식으로 변환
                    file_url = f"file://{image_info['path']}"
                    
                    all_images.append({
                        'url': file_url,
                        'folder': folder,
                        'filename': image_info['filename'],
                        'cached': True  # 캐시된 이미지임을 표시
                    })
            
            logger.info(f"캐시에서 {len(all_images)}개 이미지 로드: {product_id}")
            
            # 메인 이미지 뷰어에 이미지 로드
            self.main_image_viewer.load_product_images(all_images, product_data)
            
        except Exception as e:
            logger.error(f"캐시에서 이미지 로드 중 오류: {e}")
            # 빈 이미지로 메인 뷰어 초기화 (폴백 방식 제거)
            self.main_image_viewer.load_product_images([], product_data)
    

    
    def initialize_aws(self):
        """AWS 연결 초기화"""
        try:
            # AWS Manager 초기화
            self.aws_manager:AWSManager = AWSManager()
            
            # 연결 테스트
            connection_result = self.aws_manager.test_connection()
            
            if connection_result.get('s3', False) and connection_result.get('dynamodb', False):
                self.connection_label.setText("연결 상태: ✅ 정상")
                self.connection_label.setStyleSheet("color: #28a745; background-color: transparent; font-weight: bold;")
                
                # 각 위젯에 AWS Manager 설정
                self.main_image_viewer.set_aws_manager(self.aws_manager)
                self.main_image_viewer.set_image_cache(self.image_cache)
                self.representative_panel.set_aws_manager(self.aws_manager)
                self.representative_panel.set_image_cache(self.image_cache)
                self.representative_panel.set_main_image_viewer(self.main_image_viewer)
                
                # ProductListWidget에 AWS Manager와 이미지 캐시 설정
                self.product_list_widget.set_aws_manager(self.aws_manager)
                self.product_list_widget.set_image_cache(self.image_cache)
                
                logger.info("AWS 연결 성공")
                
                # 카테고리 선택 다이얼로그 표시(0.5초 후 카테고리 선택창 표시)
                QTimer.singleShot(500, self.show_category_selection)
                
            else:
                self.connection_label.setText("연결 상태: 오류")
                self.connection_label.setStyleSheet("color: #dc3545; background-color: transparent; font-weight: bold;")
                QMessageBox.critical(self, "연결 오류", 
                                   "AWS 연결에 실패했습니다. 설정을 확인해주세요.")
                
        except Exception as e:
            self.connection_label.setText("연결 상태: 오류")
            self.connection_label.setStyleSheet("color: #dc3545; background-color: transparent; font-weight: bold;")
            QMessageBox.critical(self, "초기화 오류", f"AWS 초기화 중 오류가 발생했습니다:\n{str(e)}")
    
    def show_category_selection(self):
        """카테고리 선택 다이얼로그 표시"""
        if not self.aws_manager:
            QMessageBox.warning(self, "AWS 연결 필요", "먼저 AWS 연결을 설정해주세요.")
            return
        
        dialog = CategorySelectionDialog(self.aws_manager, self)
        dialog.category_selected.connect(self.on_category_selected)
        
        if dialog.exec() != QDialog.Accepted:
            # 카테고리 선택이 취소된 경우 종료하거나 기본 동작 설정
            if self.selected_main_category is None:
                # 처음 실행 시 카테고리를 선택하지 않으면 앱 종료
                QMessageBox.information(self, "카테고리 선택", "카테고리를 선택해야 앱을 사용할 수 있습니다.")
                self.close()
    #CHECK : 중요 함수(카테고리 선택 완료시 상품 정보 초기화 => ProductListWidget 클래스와 연결 ) 
    def on_category_selected(self, main_category: str, sub_category: int):
        """카테고리 선택 완료 처리"""
        self.selected_main_category = main_category
        self.selected_sub_category = sub_category
        self.update_category_display()
        
        # 상태 통계 초기화 및 로드
        self.load_category_stats()
        
        # ProductListWidget에 카테고리 정보 설정(product_list_widget 인스턴스의 맴버 변수 설정(self.current_main_category, self.current_sub_category))
        self.product_list_widget.set_category_info(main_category, sub_category)
        
        # 상태 필터를 PENDING으로 설정 (진행해야 하는 작업들 우선 표시)
        self.product_list_widget.set_status_filter("PENDING")
        
        # 초기 데이터 로드
        self.load_initial_data()
    
    def update_category_display(self):
        """선택된 카테고리 정보 업데이트"""
        if self.selected_main_category and self.selected_sub_category:
            self.category_label.setText(f"카테고리: {self.selected_main_category}-{self.selected_sub_category}")
            self.category_label.setStyleSheet("color: #198754; background-color: transparent; padding-left: 20px; font-weight: bold;")
            
            # 윈도우 제목에도 카테고리 정보 추가
            self.setWindowTitle(f"AI 데이터셋 큐레이션 도구 - {self.selected_main_category}/{self.selected_sub_category}")
        else:
            self.category_label.setText("카테고리: 선택되지 않음")
            self.category_label.setStyleSheet("color: #495057; background-color: transparent; padding-left: 20px;")
            self.setWindowTitle("AI 데이터셋 큐레이션 도구")
            
            # 통계 레이블도 초기화
            self.stats_label.setText("통계: 대기 중")
            self.stats_label.setStyleSheet("color: #495057; background-color: transparent; padding-left: 20px;")
    
    def load_initial_data(self):
        """초기 데이터 로드"""
        if not self.selected_sub_category:
            QMessageBox.warning(self, "카테고리 필요", "먼저 카테고리를 선택해주세요.")
            return
        
        self.work_info_label.setText("데이터 로딩 중...")
        # self.progress_bar.setVisible(True)
        # self.progress_bar.setRange(0, 0)  # 무한 진행바
        
        # 첫 페이지 로드
        self.load_products_page(0)
    
    def load_products_page(self, page: int):
        """상품 페이지 로드"""
        if not self.selected_sub_category:
            QMessageBox.warning(self, "카테고리 필요", "먼저 카테고리를 선택해주세요.")
            return
        
        self.current_page = page
        self.work_info_label.setText(f"페이지 {page + 1} 로딩 중...")
        
        # 비동기로 상품 목록 로드 (선택된 카테고리 기반)
        self.product_list_widget.load_products_async(
            sub_category=self.selected_sub_category,
            exclusive_start_key=self.last_evaluated_key if page > 0 else None
        )
    
    def refresh_data(self):
        """데이터 새로고침"""
        if not self.selected_sub_category:
            QMessageBox.warning(self, "카테고리 필요", "먼저 카테고리를 선택해주세요.")
            return
        
        # 카테고리 통계 다시 로드
        self.load_category_stats()
        
        self.current_page = 0
        self.last_evaluated_key = None
        self.load_initial_data()
        self.main_image_viewer.clear()
        self.representative_panel.clear()
    
    def on_curation_completed(self, product_id: str):
        """큐레이션 완료 처리
        args:
            product_id(str) : 큐레이션 완료된 상품 id 
        """
        self.work_info_label.setText(f"상품 {product_id} 큐레이션 완료")
        
        # 상품 목록에서 상태 업데이트 (UI 반영만)
        self.product_list_widget.update_product_status(product_id, "COMPLETED")
        
        # 현재 제품 데이터의 상태도 업데이트 (UI 반영만)
        current_product = self.representative_panel.current_product if hasattr(self.representative_panel, 'current_product') else None
        if current_product and current_product.get('product_id') == product_id:
            previous_status = current_product.get('current_status', 'PENDING')
            current_product['current_status'] = 'COMPLETED'
            
            # 로컬 통계 업데이트
            self.update_local_stats(previous_status, 'COMPLETED')
        
        # 2초 후 상태 메시지 리셋
        QTimer.singleShot(2000, lambda: self.work_info_label.setText("준비 완료"))
    
    def on_product_passed(self, product_id: str):
        """상품 보류 처리 완료
        args:
            product_id(str) : 보류 처리된 상품 id 
        """
        self.work_info_label.setText(f"상품 {product_id} 보류 처리 완료")
        
        # 상품 목록에서 상태 업데이트 (UI 반영만)
        self.product_list_widget.update_product_status(product_id, "PASS")
        
        # 현재 제품 데이터의 상태도 업데이트 (UI 반영만)
        current_product = self.representative_panel.current_product if hasattr(self.representative_panel, 'current_product') else None
        if current_product and current_product.get('product_id') == product_id:
            previous_status = current_product.get('current_status', 'PENDING')
            current_product['current_status'] = 'PASS'
            
            # 로컬 통계 업데이트
            self.update_local_stats(previous_status, 'PASS')
        
        # 2초 후 상태 메시지 리셋
        QTimer.singleShot(2000, lambda: self.work_info_label.setText("준비 완료"))
    
    def update_local_stats(self, previous_status: str, new_status: str):
        """로컬 통계 업데이트"""
        if previous_status == new_status:
            return  # 동일한 상태로 변경하는 경우 무시
        
        # 이전 상태 카운트 감소
        if previous_status.lower() in self.current_stats:
            self.current_stats[previous_status.lower()] = max(0, self.current_stats[previous_status.lower()] - 1)
        
        # 새 상태 카운트 증가
        if new_status.lower() in self.current_stats:
            self.current_stats[new_status.lower()] += 1
        
        # 상태바 통계 표시 업데이트
        self.update_stats_display()
        
        logger.info(f"로컬 통계 업데이트: {previous_status} -> {new_status}, 현재 통계: {self.current_stats}")
    
    def show_statistics(self):
        """통계 정보 표시 - 새로운 상태 통계 시스템 사용"""
        if not self.aws_manager:
            return
        
        try:
            # 모든 카테고리 상태 통계 조회
            all_stats = self.aws_manager.get_all_category_status_stats()
            
            stats_text = "작업 통계:\n\n"
            
            if all_stats:
                # 전체 통계
                total_products = sum(stats.get('total', 0) for stats in all_stats.values())
                total_pending = sum(stats.get('pending', 0) for stats in all_stats.values())
                total_completed = sum(stats.get('completed', 0) for stats in all_stats.values())
                total_pass = sum(stats.get('pass', 0) for stats in all_stats.values())
                
                stats_text += f"전체 통계:\n"
                stats_text += f"• 총 제품 수: {total_products:,}개\n"
                stats_text += f"• 미정: {total_pending:,}개 ({total_pending/total_products*100:.1f}%)\n" if total_products > 0 else "• 미정: 0개\n"
                stats_text += f"• 완료: {total_completed:,}개 ({total_completed/total_products*100:.1f}%)\n" if total_products > 0 else "• 완료: 0개\n"
                stats_text += f"• 보류: {total_pass:,}개 ({total_pass/total_products*100:.1f}%)\n\n" if total_products > 0 else "• 보류: 0개\n\n"
                
                # 선택된 카테고리 통계
                if self.selected_main_category and self.selected_sub_category:
                    category_key = f"{self.selected_main_category}_{self.selected_sub_category}"
                    if category_key in all_stats:
                        cat_stats = all_stats[category_key]
                        stats_text += f"현재 선택된 카테고리 ({self.selected_main_category}-{self.selected_sub_category}):\n"
                        stats_text += f"• 전체: {cat_stats.get('total', 0):,}개\n"
                        stats_text += f"• 미정: {cat_stats.get('pending', 0):,}개\n"
                        stats_text += f"• 완료: {cat_stats.get('completed', 0):,}개\n"
                        stats_text += f"• 보류: {cat_stats.get('pass', 0):,}개\n\n"
                
                # 카테고리별 상세 통계
                stats_text += "카테고리별 상세 통계:\n"
                # 메인 카테고리별로 그룹화
                main_categories = {}
                for category_key, stats in all_stats.items():
                    try:
                        main_cat = category_key.split('_')[0]
                        if main_cat not in main_categories:
                            main_categories[main_cat] = []
                        main_categories[main_cat].append((category_key, stats))
                    except IndexError:
                        continue
                
                for main_cat, cat_list in sorted(main_categories.items()):
                    stats_text += f"\n{main_cat} 카테고리:\n"
                    for category_key, stats in sorted(cat_list):
                        sub_cat = category_key.split('_')[1] if '_' in category_key else 'Unknown'
                        stats_text += f"  - {sub_cat}: {stats.get('total', 0):,}개 "
                        stats_text += f"(미정: {stats.get('pending', 0)}, 완료: {stats.get('completed', 0)}, 보류: {stats.get('pass', 0)})\n"
            else:
                stats_text += "카테고리 상태 통계를 찾을 수 없습니다.\n초기 데이터 업로드를 먼저 실행해주세요.\n"
            
            QMessageBox.information(self, "작업 통계", stats_text)
            
        except Exception as e:
            QMessageBox.warning(self, "통계 오류", f"통계를 가져오는 중 오류가 발생했습니다:\n{str(e)}")
    
    def on_page_images_preloaded(self):
        """페이지 이미지 프리로딩 완료 처리"""
        self.work_info_label.setText("페이지 이미지 로딩 완료")
        # 3초 후 상태 메시지 리셋
        QTimer.singleShot(3000, lambda: self.work_info_label.setText("준비 완료"))
    
    def clear_cache(self):
        """이미지 캐시 정리"""
        self.image_cache.clear_all_cache()
        self.work_info_label.setText("캐시가 정리되었습니다.")
        QTimer.singleShot(3000, lambda: self.work_info_label.setText("준비 완료"))
    
    def keyPressEvent(self, event: QKeyEvent):
        """전역 키보드 단축키 처리"""
        key = event.key()
        modifiers = event.modifiers()
        
        # 현재 포커스된 위젯이 텍스트 입력 위젯인 경우 단축키 무시
        focused_widget = QApplication.focusWidget()
        if focused_widget and isinstance(focused_widget, (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox)):
            super().keyPressEvent(event)
            return
        
        try:
            # Tab: 탭 이동 (MainImageViewer)
            if key == Qt.Key_Tab:
                if hasattr(self, 'main_image_viewer') and self.main_image_viewer:
                    self.main_image_viewer.keyPressEvent(event)
                    if event.isAccepted():
                        return
            
            # Ctrl+Z: 되돌리기 (MainImageViewer)
            elif key == Qt.Key_Z and modifiers == Qt.ControlModifier:
                if hasattr(self, 'main_image_viewer') and self.main_image_viewer:
                    self.main_image_viewer.keyPressEvent(event)
                    if event.isAccepted():
                        return
            
            # M/m: Text 폴더로 이동 (MainImageViewer)
            elif key == Qt.Key_M:
                if hasattr(self, 'main_image_viewer') and self.main_image_viewer:
                    self.main_image_viewer.keyPressEvent(event)
                    if event.isAccepted():
                        return
            
            # Space: 큐레이션 완료 (RepresentativePanel)
            elif key == Qt.Key_Space:
                if hasattr(self, 'representative_panel') and self.representative_panel:
                    self.representative_panel.keyPressEvent(event)
                    if event.isAccepted():
                        return
            
            # 1,2,3,4: 대표 이미지 모드 선택 (MainImageViewer)
            elif key in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4):
                if hasattr(self, 'main_image_viewer') and self.main_image_viewer:
                    self.main_image_viewer.keyPressEvent(event)
                    if event.isAccepted():
                        return
            
            # ESC: 선택 모드 취소 (MainImageViewer)
            elif key == Qt.Key_Escape:
                if hasattr(self, 'main_image_viewer') and self.main_image_viewer:
                    self.main_image_viewer.keyPressEvent(event)
                    if event.isAccepted():
                        return
            
            # V: 이미지 뷰어 열기 (MainImageViewer)
            elif key == Qt.Key_V:
                if hasattr(self, 'main_image_viewer') and self.main_image_viewer:
                    self.main_image_viewer.keyPressEvent(event)
                    if event.isAccepted():
                        return
            
            # F5: 새로고침
            elif key == Qt.Key_F5:
                self.refresh_data()
                event.accept()
                return
            
            # Ctrl+C: 카테고리 변경
            elif key == Qt.Key_C and modifiers == Qt.ControlModifier:
                self.show_category_selection()
                event.accept()
                return
            
            # Ctrl+Q: 종료
            elif key == Qt.Key_Q and modifiers == Qt.ControlModifier:
                self.close()
                event.accept()
                return
                
        except Exception as e:
            logger.error(f"전역 키보드 이벤트 처리 오류: {str(e)}")
        
        # 처리되지 않은 키는 부모 클래스로 전달
        super().keyPressEvent(event)
    
    def load_category_stats(self):
        """카테고리 상태 통계를 로드합니다."""
        if not self.aws_manager or not self.selected_main_category or not self.selected_sub_category:
            return
        
        try:
            stats = self.aws_manager.get_category_quick_stats(
                self.selected_main_category, self.selected_sub_category
            )
            
            # 현재 통계 설정 - 키를 소문자로 정규화
            self.current_stats = {}
            for key, value in stats.items():
                self.current_stats[key.lower()] = value
            
            # UI 업데이트
            self.update_stats_display()
            
            logger.info(f"카테고리 통계 로드 완료: {self.current_stats}")
            
        except Exception as e:
            logger.error(f"카테고리 통계 로드 실패: {e}")
            # 기본값으로 설정
            self.current_stats = {'pending': 0, 'completed': 0, 'pass': 0, 'total': 0}
            self.update_stats_display()
    
    def update_stats_display(self):
        """통계 정보를 상태바에 표시합니다."""
        pending = self.current_stats['pending']
        completed = self.current_stats['completed']
        pass_count = self.current_stats['pass']
        total = self.current_stats['total']
        
        stats_text = f"통계: 전체 {total} | 미정 {pending} | 완료 {completed} | 보류 {pass_count}"
        
        self.stats_label.setText(stats_text)
        self.stats_label.setStyleSheet("color: #ffffff; background-color: #007bff; padding: 5px 15px; border-radius: 3px; font-weight: bold;")
    
    def closeEvent(self, event):
        """애플리케이션 종료 시 정리 작업"""
        logger.info("애플리케이션 종료 시작 - 정리 작업 수행 중...")
        
        try:
            # 캐시 정리
            if hasattr(self, 'image_cache') and self.image_cache:
                try:
                    self.image_cache.cleanup()
                    logger.info("이미지 캐시 정리 완료")
                except Exception as e:
                    logger.error(f"이미지 캐시 정리 중 오류: {str(e)}")
            
            # 스레드 정리
            if hasattr(self, 'product_list_widget') and self.product_list_widget:
                try:
                    self.product_list_widget.cleanup()
                    logger.info("상품 목록 위젯 정리 완료")
                except Exception as e:
                    logger.error(f"상품 목록 위젯 정리 중 오류: {str(e)}")
            
            # 메인 이미지 뷰어 정리
            if hasattr(self, 'main_image_viewer') and self.main_image_viewer:
                try:
                    self.main_image_viewer.clear()
                    logger.info("메인 이미지 뷰어 정리 완료")
                except Exception as e:
                    logger.error(f"메인 이미지 뷰어 정리 중 오류: {str(e)}")
            
            # 대표 이미지 패널 정리
            if hasattr(self, 'representative_panel') and self.representative_panel:
                try:
                    self.representative_panel.cleanup()
                    logger.info("대표 이미지 패널 정리 완료")
                except Exception as e:
                    logger.error(f"대표 이미지 패널 정리 중 오류: {str(e)}")
            
            # AWS 매니저 정리
            if hasattr(self, 'aws_manager') and self.aws_manager:
                try:
                    # AWS 매니저에 cleanup 메서드가 있다면 호출
                    if hasattr(self.aws_manager, 'cleanup'):
                        self.aws_manager.cleanup()
                    logger.info("AWS 매니저 정리 완료")
                except Exception as e:
                    logger.error(f"AWS 매니저 정리 중 오류: {str(e)}")
            
            # 위젯 참조 정리
            self.product_list_widget = None
            self.main_image_viewer = None
            self.representative_panel = None
            self.aws_manager = None
            self.image_cache = None
            
            logger.info("모든 정리 작업 완료 - 애플리케이션을 종료합니다.")
            
        except Exception as e:
            logger.error(f"애플리케이션 종료 중 예상치 못한 오류: {str(e)}")
        
        event.accept()


def main():
    """메인 함수"""
    # Qt 애플리케이션 생성
    app = QApplication(sys.argv)
    
    # 애플리케이션 정보 설정
    app.setApplicationName("AI 데이터셋 큐레이션 도구")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("AI Data Team")
    
    # 메인 윈도우 생성 및 표시
    window = MainWindow()
    window.show()
    
    # 이벤트 루프 실행
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
    


