#!/usr/bin/env python3
"""
대표 이미지 패널 위젯
선정된 대표 이미지들을 표시하고 관리합니다.
"""

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                               QPushButton, QScrollArea, QFrame, QGridLayout,
                               QButtonGroup, QCheckBox, QComboBox, QMessageBox,
                               QTextEdit, QSpacerItem, QSizePolicy, QDialog,
                               QRadioButton)
from PySide6.QtCore import Qt, Signal, QSize, QTimer, QCoreApplication
from PySide6.QtGui import QPixmap, QFont, QColor, QPainter, QPen, QKeyEvent
from typing import Dict, Any, List, Optional
import logging
import os

# 분리된 모듈들 import
from .pass_reason_dialog import PassReasonDialog
from .curation_confirm_dialog import CurationConfirmDialog
from .image_widgets import PlaceholderImageWidget, RepresentativeImageWidget
from aws_manager import AWSManager

# CurationWorker import 추가
from .main_image_viewer import CurationWorker

logger = logging.getLogger(__name__)


class RepresentativePanel(QWidget):
    """대표 이미지 패널 위젯 \n
    - curation_completed : Signal(str) 큐레이션 완료 시 상품 ID 전달
    - product_passed : Signal(str) 상품 보류 처리 시 상품 ID 전달
    """
    
    curation_completed = Signal(str)  # 완료된 상품 ID
    product_passed = Signal(str)  # 보류된 상품 ID
    
    def __init__(self):
        super().__init__()
        self.aws_manager:'AWSManager' = None
        self.image_cache = None
        self.main_image_viewer = None  # MainImageViewer 참조 추가
        self.current_product = None
        self.representative_images = {}  # 대표 이미지 3개 (model_wearing, front_cutout, back_cutout)
        self.color_variant_images = {}  # 색상별 정면 누끼 이미지들
        self.curation_worker = None  # S3 업데이트 워커
        self._is_destroyed = False  # 위젯 파괴 상태 추적
        
        # 키보드 포커스 설정
        self.setFocusPolicy(Qt.StrongFocus)
        
        self.setup_ui()
    
    def closeEvent(self, event):
        """위젯 닫힐 때 호출"""
        self._is_destroyed = True
        self.cleanup()
        super().closeEvent(event)

    def deleteLater(self):
        """위젯 삭제 예정 시 호출"""
        self._is_destroyed = True
        self.cleanup()
        super().deleteLater()
    
    def setup_ui(self):
        """UI 설정"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        
        # 헤더
        self.setup_header(layout)
        
        # 대표 이미지 영역 (모델, 정면 누끼, 후면 누끼)
        self.setup_main_representative_area(layout, stretch=1)
        
        # 제품 색상 영역 (여러 색상의 정면 누끼)
        self.setup_color_variants_area(layout, stretch=1)
        
        # 하단 컨트롤
        self.setup_bottom_controls(layout)
    
    def setup_header(self, parent_layout):
        """헤더 설정"""
        header_frame = QFrame()
        header_frame.setStyleSheet("background-color: #f8f9fa; color: #212529; border-bottom: 1px solid #dee2e6; border-radius: 5px;")
        header_layout = QHBoxLayout(header_frame)  # QVBoxLayout에서 QHBoxLayout으로 변경
        header_layout.setContentsMargins(10, 5, 10, 5)
        
        # 제목
        title_label = QLabel("대표 이미지")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(10)
        title_label.setFont(title_font)
        header_layout.addWidget(title_label)
        
        header_layout.addStretch(5)  # 제목과 상품 정보 사이에 공간 추가
        
        # 상품 정보
        self.product_info_label = QLabel("상품을 선택해주세요")
        self.product_info_label.setStyleSheet("color: #495057; background-color: transparent; font-size: 8px;")
        header_layout.addWidget(self.product_info_label)
        
        parent_layout.addWidget(header_frame)
    
    def setup_main_representative_area(self, parent_layout, stretch=1):
        """대표 이미지 영역 설정 (모델, 정면 누끼, 후면 누끼)"""
        main_frame = QFrame()
        main_frame.setStyleSheet("background-color: #e8f5e8; color: #212529; border: 2px solid #28a745; border-radius: 5px;")
        main_layout = QVBoxLayout(main_frame)
        main_layout.setContentsMargins(5, 5, 5, 5)
        
        # 제목 (높이 비율: 1)
        main_title = QLabel("대표 이미지 (대표 색상)")
        main_title.setStyleSheet("font-weight: bold; color: #155724; background-color: transparent; font-size: 14px; padding-bottom: 5px;")
        main_layout.addWidget(main_title, 1)  # stretch=1로 비율 설정
        
        # 설명
        # desc_label = QLabel("동일한 색상의 모델 착용, 정면 누끼, 후면 누끼 이미지를 선정해주세요.")
        # desc_label.setStyleSheet("color: #495057; background-color: transparent; font-size: 11px; padding-bottom: 10px;")
        # desc_label.setWordWrap(True)
        # main_layout.addWidget(desc_label)
        
        # 대표 이미지 그리드 (높이 비율: 8 - 가장 큰 비중)
        self.main_rep_grid_widget = QWidget()
        self.main_rep_grid_layout = QHBoxLayout(self.main_rep_grid_widget)
        self.main_rep_grid_layout.setSpacing(5)
        self.main_rep_grid_layout.setContentsMargins(5, 5, 5, 5)
        
        main_layout.addWidget(self.main_rep_grid_widget, 8)  # stretch=8로 높은 비중
        
        # 상태 표시 (높이 비율: 1)
        self.main_status_label = QLabel("대표 이미지 3개를 선정해주세요")
        self.main_status_label.setAlignment(Qt.AlignCenter)
        self.main_status_label.setStyleSheet("color: #155724; background-color: #d4edda; font-size: 10px; padding: 3px; border-radius: 3px;")
        main_layout.addWidget(self.main_status_label, 1)  # stretch=1로 비율 설정
        
        parent_layout.addWidget(main_frame, stretch)
    
    def setup_color_variants_area(self, parent_layout, stretch=2):
        """제품 색상 영역 설정 (여러 색상의 정면 누끼)"""
        color_frame = QFrame()
        color_frame.setStyleSheet("background-color: #e3f2fd; color: #212529; border: 2px solid #007bff; border-radius: 2px;")
        color_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        color_layout = QVBoxLayout(color_frame)
        color_layout.setContentsMargins(5, 5, 5, 5)
        
        # 제목
        color_title = QLabel("제품 색상")
        color_title.setStyleSheet("font-weight: bold; color: #0c4a60; background-color: transparent; font-size: 10px; padding-bottom: 2px;")
        color_layout.addWidget(color_title)
        
        # 설명
        desc_label = QLabel("대표 이미지 3개 선정 후, 다른 색상의 정면 누끼 이미지를 최소 1개 이상 선택.")
        desc_label.setStyleSheet("color: #495057; background-color: transparent; font-size: 11px; padding-bottom: 2px;")
        desc_label.setWordWrap(True)
        color_layout.addWidget(desc_label)
        
        # 스크롤 영역
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setMinimumHeight(150)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # 스크롤바 스타일 설정 - 움직이는 바(thumb)를 더 잘 보이게 하기 위해 색상 반전
        scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: transparent;
            }
            QScrollBar:horizontal {
                background-color: #f0f0f0;
                height: 12px;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal {
                background-color: #007bff;
                border-radius: 6px;
                min-width: 20px;
            }
            QScrollBar::handle:horizontal:hover {
                background-color: #0056b3;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                background-color: transparent;
                width: 0px;
            }
            QScrollBar:vertical {
                background-color: #f0f0f0;
                width: 12px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background-color: #007bff;
                border-radius: 6px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #0056b3;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                background-color: transparent;
                height: 0px;
            }
        """)
        
        # 색상별 이미지 그리드
        self.color_grid_widget = QWidget()
        self.color_grid_layout = QHBoxLayout(self.color_grid_widget)
        self.color_grid_layout.setSpacing(10)
        self.color_grid_layout.setContentsMargins(5, 5, 5, 5)
        self.color_grid_layout.addStretch()
        
        scroll_area.setWidget(self.color_grid_widget)
        color_layout.addWidget(scroll_area)
        
        # 상태 표시
        self.color_status_label = QLabel("대표 이미지 3개를 먼저 선정해주세요")
        self.color_status_label.setAlignment(Qt.AlignCenter)
        self.color_status_label.setStyleSheet("color: #0c4a60; background-color: #d1ecf1; font-size: 8px; padding: 3px; border-radius: 3px;")
        color_layout.addWidget(self.color_status_label)
        
        parent_layout.addWidget(color_frame, stretch)
    
    def setup_bottom_controls(self, parent_layout):
        """하단 컨트롤 설정"""
        controls_frame = QFrame()
        controls_frame.setStyleSheet("background-color: #f8f9fa; color: #212529; border-top: 1px solid #dee2e6; border-radius: 3px;")
        controls_layout = QVBoxLayout(controls_frame)
        controls_layout.setContentsMargins(5, 5, 5, 5)
        
        # 선택 요약
        self.selection_summary = QLabel("선택된 대표 이미지: 0개")
        self.selection_summary.setStyleSheet("font-weight: bold; color: #212529; background-color: transparent; padding-bottom: 2px;")
        controls_layout.addWidget(self.selection_summary)
        
        # 버튼 영역
        button_layout = QHBoxLayout()
        
        # 초기화 버튼
        clear_btn = QPushButton("초기화")
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                padding: 4px 8px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #545b62;
            }
        """)
        clear_btn.clicked.connect(self.clear_representatives)
        button_layout.addWidget(clear_btn)
        
        # Pass 버튼
        self.pass_btn = QPushButton("Pass (보류)")
        self.pass_btn.setStyleSheet("""
            QPushButton {
                background-color: #ffc107;
                color: #212529;
                border: none;
                padding: 4px 8px;
                border-radius: 3px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #e0a800;
            }
        """)
        self.pass_btn.clicked.connect(self.pass_product)
        self.pass_btn.setEnabled(False)  # 초기에는 비활성화
        button_layout.addWidget(self.pass_btn)
        
        button_layout.addStretch()
        
        # 완료 버튼
        self.complete_btn = QPushButton("큐레이션 완료 (Space)")
        self.complete_btn.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                padding: 4px 8px;
                border-radius: 3px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #218838;
            }
            QPushButton:disabled {
                background-color: #6c757d;
            }
        """)
        self.complete_btn.clicked.connect(self.complete_curation)
        self.complete_btn.setEnabled(False)
        button_layout.addWidget(self.complete_btn)
        
        controls_layout.addLayout(button_layout)
        
        parent_layout.addWidget(controls_frame)
    
    def set_aws_manager(self, aws_manager:AWSManager):
        """AWS 매니저 설정"""
        self.aws_manager:AWSManager = aws_manager
    
    def set_image_cache(self, image_cache):
        """이미지 캐시 설정"""
        self.image_cache = image_cache
    
    def set_main_image_viewer(self, main_image_viewer):
        """메인 이미지 뷰어 참조 설정"""
        self.main_image_viewer = main_image_viewer
    
    def load_product(self, product_data: Dict[str, Any]):
        """
        우측 패널 위젯에 상품 정보 로드(상품 id 정보, 기존 대표 이미지 삭제 및 새로운 대표 이미지 추가, 상태 업데이트)
        args:
            product_data(dict) : dynamoDB에서 조회한 상품 개별 딕셔너리 정보(좌측 패널 위젯에서 특정 상품 클릭시 데이터 전달받음) \n
                                 ProductListWidget 클래스에서 정의한 커스텀 Signal이 전송하는 데이터 
        return:
            None
        """
        self.current_product = product_data
        
        # 상품 정보 업데이트
        product_id = product_data.get('product_id', 'Unknown')
        self.product_info_label.setText(f"상품 ID: {product_id}")
        
        # 이전 선택 초기화
        self.representative_images = {}
        self.color_variant_images = {}
        
        # Pass 버튼 활성화 (상품이 로드되면 언제든지 Pass 가능)
        self.pass_btn.setEnabled(True)
        
        self.update_display()
    
    def is_main_representative_complete(self) -> bool:
        """대표 이미지 3개가 모두 선택되었는지 확인"""
        required_types = {'model_wearing', 'front_cutout', 'back_cutout'}
        selected_types = set(self.representative_images.keys())
        return required_types == selected_types
    
    def get_missing_main_types(self) -> List[str]:
        """대표 이미지에서 누락된 타입들 반환"""
        required_types = {'model_wearing', 'front_cutout', 'back_cutout'}
        selected_types = set(self.representative_images.keys())
        missing_types = required_types - selected_types
        
        type_names = {
            'model_wearing': '모델 착용',
            'front_cutout': '정면 누끼',
            'back_cutout': '후면 누끼'
        }
        
        return [type_names.get(t, t) for t in missing_types]
    
    def add_representative_image(self, image_data: Dict[str, Any], image_type: str):
        """대표 이미지 추가(메인 이미지 뷰어에서 대표 이미지 선정 후 버튼 누른 경우 선택된 이미지 데이터 및 타입 전달)
        args:
            image_data(dict) : 대표 이미지 데이터(딕셔너리) \n
            image_type(str) : 대표 이미지 타입(문자열) \n
        return:
            None
        """
        if image_type in ['model_wearing', 'front_cutout', 'back_cutout']:
            # 대표 이미지 추가
            self.representative_images[image_type] = image_data
            type_name = self.get_type_display_name(image_type)
            self.main_status_label.setText(f"{type_name} 이미지가 대표 이미지로 선정되었습니다")
        elif image_type in ['color_variant', 'color_variant_front']:
            # 색상 변형 이미지 추가 (정면 누끼만)
            if not self.is_main_representative_complete():
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "경고", "대표 이미지 3개를 먼저 선정해주세요.")
                return
            
            # 색상 변형 이미지는 순서대로 저장 (color_1, color_2, ...)
            color_index = len(self.color_variant_images) + 1
            color_key = f"color_{color_index}"
            self.color_variant_images[color_key] = image_data
            self.color_status_label.setText(f"색상 변형 {color_index}번 이미지가 추가되었습니다")
        
        self.update_display()
    
    def remove_representative_image(self, image_key: str):
        """대표 이미지 제거"""
        # 대표 이미지에서 제거 시도
        if image_key in self.representative_images:
            del self.representative_images[image_key]
            type_name = self.get_type_display_name(image_key)
            self.main_status_label.setText(f"{type_name} 이미지가 제거되었습니다")
            self.update_display()
            return
        
        # 색상 변형 이미지에서 제거 시도
        if image_key in self.color_variant_images:
            del self.color_variant_images[image_key]
            # 색상 변형 이미지 키 재정렬
            self._reorder_color_variants()
            self.color_status_label.setText(f"색상 변형 이미지가 제거되었습니다")
            self.update_display()
            return
    
    def _reorder_color_variants(self):
        """색상 변형 이미지 키 재정렬"""
        variants = list(self.color_variant_images.values())
        self.color_variant_images = {}
        for i, image_data in enumerate(variants, 1):
            self.color_variant_images[f"color_{i}"] = image_data
    
    def get_type_display_name(self, image_type: str) -> str:
        """타입별 표시명 반환"""
        type_names = {
            'model_wearing': '모델 착용',
            'front_cutout': '정면 누끼',
            'back_cutout': '후면 누끼',
            'color_variant': '제품 색상'
        }
        return type_names.get(image_type, image_type)
    
    def update_display(self):
        """
        이 update_display() 함수는 대표 이미지 패널의 화면을 새로고침하는 역할을 합니다.
        주요 동작: \n
            - 기존 위젯 제거: 모든 기존 대표 이미지 위젯들을 역순으로 순회하며 삭제 \n
            - 메모리 정리: deleteLater()를 사용해 위젯을 안전하게 제거 \n
            - 화면 갱신 준비: 새로운 대표 이미지들을 표시할 수 있도록 레이아웃을 초기화 \n
        사용 시점: \n
            - 새로운 대표 이미지가 추가될 때 \n
            - 대표 이미지가 제거될 때 \n
            - 상품이 변경될 때 \n
            - 초기화할 때 \n
        이 함수는 UI의 일관성을 유지하고 메모리 누수를 방지하는 중요한 역할을 합니다. \n
        return:
            None
        """
        # 대표 이미지 영역 업데이트 - 고정된 순서로 배치
        self.clear_layout(self.main_rep_grid_layout)
        
        # 고정된 순서 정의: 모델 -> 정면 -> 후면
        image_types_order = ['model_wearing', 'front_cutout', 'back_cutout']
        
        for image_type in image_types_order:
            if image_type in self.representative_images:
                # 선택된 이미지가 있는 경우
                image_data = self.representative_images[image_type]
                rep_widget = RepresentativeImageWidget(image_data, image_type, True, self.image_cache)
                rep_widget.remove_requested.connect(self.remove_representative_image)
                self.main_rep_grid_layout.addWidget(rep_widget)
            else:
                # 선택되지 않은 타입에 대해서는 플레이스홀더 표시
                placeholder_widget = PlaceholderImageWidget(image_type)
                self.main_rep_grid_layout.addWidget(placeholder_widget)
        
        # 색상 변형 영역 업데이트
        self.clear_layout(self.color_grid_layout)
        # 색상 변형 이미지들을 정렬된 순서로 추가
        sorted_color_keys = sorted(self.color_variant_images.keys(), key=lambda x: int(x.split('_')[1]))
        for image_key in sorted_color_keys:
            image_data = self.color_variant_images[image_key]
            variant_widget = RepresentativeImageWidget(image_data, image_key, False, self.image_cache)
            variant_widget.remove_requested.connect(self.remove_representative_image)
            self.color_grid_layout.addWidget(variant_widget)
        self.color_grid_layout.addStretch()
        
        # 상태 업데이트
        self.update_status()
    
    def clear_layout(self, layout):
        """레이아웃 정리"""
        try:
            while layout.count():
                child = layout.takeAt(0)
                if child.widget():
                    widget = child.widget()
                    try:
                        # 위젯 정리
                        if hasattr(widget, 'cleanup'):
                            widget.cleanup()
                        elif hasattr(widget, '_is_destroyed'):
                            widget._is_destroyed = True
                        
                        # 위젯 삭제
                        if widget.parent():
                            widget.setParent(None)
                        widget.deleteLater()
                        
                    except Exception as e:
                        logger.warning(f"레이아웃 위젯 정리 중 오류: {str(e)}")
                        continue
                elif child.spacerItem():
                    # 스페이서 아이템 제거
                    pass
        except Exception as e:
            logger.error(f"레이아웃 정리 오류: {str(e)}")
    
    def update_status(self):
        """상태 정보 업데이트"""
        main_count = len(self.representative_images)
        color_count = len(self.color_variant_images)
        total_count = main_count + color_count
        
        self.selection_summary.setText(f"선택된 이미지: 대표 {main_count}개, 색상 변형 {color_count}개")
        
        # 대표 이미지 3개 완성 여부 확인
        is_main_complete = self.is_main_representative_complete()
        
        # 완료 버튼 활성화 조건: 대표 이미지 3개 + 색상 변형 이미지 1개 이상
        is_complete = is_main_complete and color_count > 0
        self.complete_btn.setEnabled(is_complete)
        
        # 대표 이미지 영역 상태 업데이트
        if main_count == 0:
            self.main_status_label.setText("대표 이미지 3개를 선정해주세요")
        elif not is_main_complete:
            missing_types = self.get_missing_main_types()
            self.main_status_label.setText(f"{', '.join(missing_types)} 이미지를 선정해주세요")
        else:
            self.main_status_label.setText("대표 이미지 3개 선정 완료!")
        
        # 색상 변형 영역 상태 업데이트
        if not is_main_complete:
            self.color_status_label.setText("대표 이미지 3개를 먼저 선정해주세요")
        elif color_count == 0:
            self.color_status_label.setText("큐레이션 완료를 위해 색상 변형 이미지를 최소 1개 이상 선택해주세요")
        else:
            self.color_status_label.setText(f"{color_count}개의 색상 변형 이미지가 추가되었습니다 (큐레이션 완료 가능)")
    
    def clear_representatives(self):
        """대표 이미지들 초기화"""
        try:
            # 데이터 초기화
            self.representative_images = {}
            self.color_variant_images = {}
            
            # 디스플레이 업데이트 (기존 위젯들이 자동으로 정리됨)
            self.update_display()
            
            # 상태 업데이트
            self.update_status()
            
            logger.info("대표 이미지 초기화 완료")
            
        except Exception as e:
            logger.error(f"대표 이미지 초기화 오류: {str(e)}")
    
    def keyPressEvent(self, event):
        """키보드 이벤트 처리"""
        try:
            # Space: 큐레이션 완료 (버튼이 활성화된 경우에만)
            if event.key() == Qt.Key_Space:
                if self.complete_btn.isEnabled():
                    self.complete_curation()
                    event.accept()
                    return
                else:
                    # 버튼이 비활성화된 경우 안내 메시지
                    self.show_status_message("❌ 대표 이미지 3개와 색상 변형 1개 이상을 선택해주세요")
                    event.accept()
                    return
            
        except Exception as e:
            logger.error(f"RepresentativePanel 키보드 이벤트 처리 오류: {str(e)}")
        
        # 처리되지 않은 키는 부모 클래스로 전달
        super().keyPressEvent(event)
    
    def show_status_message(self, message: str, error: bool = False):
        """상태 메시지 표시 - 임시로 selection_summary에 표시"""
        try:
            original_text = self.selection_summary.text()
            self.selection_summary.setText(message)
            self.selection_summary.setStyleSheet("font-weight: bold; color: #dc3545; background-color: transparent; padding-bottom: 10px;")
            
            # 1초 후 원래 메시지로 복원
            def restore_message():
                self.selection_summary.setText(original_text)
                self.selection_summary.setStyleSheet("font-weight: bold; color: #212529; background-color: transparent; padding-bottom: 10px;")
            
            QTimer.singleShot(1000, restore_message)
            
        except Exception as e:
            logger.error(f"상태 메시지 표시 오류: {str(e)}")
    
    def show_complete_success_status(self, product_id: str = None):
        """큐레이션 완료 성공 상태를 패널 내에서 시각적으로 표시"""
        try:
            if product_id is None:
                product_id = self.current_product.get('product_id', 'Unknown') if self.current_product else 'Unknown'
            
            # 메인 선택 요약에 성공 메시지 표시
            self.selection_summary.setText(f"✅ 큐레이션 완료! 상품 ID: {product_id}")
            self.selection_summary.setStyleSheet("font-weight: bold; color: #28a745; background-color: #d4edda; padding: 8px; border-radius: 4px; border: 1px solid #c3e6cb;")
            
            # 대표 이미지 영역 상태 업데이트
            self.main_status_label.setText("✅ 대표 이미지 3개 큐레이션 완료!")
            self.main_status_label.setStyleSheet("color: #155724; background-color: #d4edda; font-size: 11px; padding: 6px; border-radius: 3px; font-weight: bold;")
            
            # 색상 변형 영역 상태 업데이트
            self.color_status_label.setText("✅ 모든 색상 변형 이미지 큐레이션 완료!")
            self.color_status_label.setStyleSheet("color: #0c4a60; background-color: #d1ecf1; font-size: 11px; padding: 6px; border-radius: 3px; font-weight: bold;")
            
            # 완료 버튼을 성공 상태로 변경
            self.complete_btn.setText("✅ 큐레이션 완료됨")
            self.complete_btn.setStyleSheet("""
                QPushButton {
                    background-color: #28a745;
                    color: white;
                    border: 2px solid #20c997;
                    padding: 8px 16px;
                    border-radius: 4px;
                    font-weight: bold;
                }
            """)
            self.complete_btn.setEnabled(False)
            
            # 즉시 패널 초기화
            self._reset_panel_after_completion()

            # PASS 처리 후에도 메인 이미지 뷰어의 선택 모드를 강제로 초기화
            if self.main_image_viewer:
                self.main_image_viewer.clear_selection_mode()
                self.main_image_viewer.setFocus()
                self.main_image_viewer.setFocusPolicy(Qt.StrongFocus)
            
        except Exception as e:
            logger.error(f"큐레이션 완료 성공 상태 표시 오류: {str(e)}")
    # CHECK : 중요 함수(상품 PASS시에 보류 처리)
    def pass_product(self):
        """상품을 보류(Pass) 상태로 처리"""
        if not self.current_product:
            QMessageBox.warning(self, "오류", "상품 정보가 없습니다.")
            return
        
        if not self.aws_manager:
            QMessageBox.warning(self, "오류", "AWS 연결이 설정되지 않았습니다.")
            return
        
        # Pass 이유 입력 다이얼로그 표시
        dialog = PassReasonDialog(self.current_product.get('product_id', 'Unknown'), self)
        if dialog.exec() != QDialog.Accepted:
            return
            
        pass_reason = dialog.selected_reason
        
        # 초기화되기 전에 필요한 값들을 미리 저장
        product_id = self.current_product.get('product_id', '')
        sub_category = self.current_product.get('sub_category')
        main_category = self.current_product.get('main_category')
        previous_status = self.current_product.get('current_status', 'PENDING')
        
        # 버튼 상태 변경
        original_text = self.pass_btn.text()
        self.pass_btn.setText("🔄 처리 중...")
        self.pass_btn.setEnabled(False)
        
        success = False
        
        try:
            
            # DynamoDB에 PASS 상태와 이유 저장 (completed_by는 자동으로 현재 AWS 사용자로 설정됨)
            success = self.aws_manager.update_product_status_to_pass(
                sub_category=sub_category,
                product_id=product_id,
                pass_reason=pass_reason  # Pass 이유 추가
            )
            
            if success:
                # 상태 통계 업데이트 (이전 상태에서 PASS로 변경)
                if previous_status != 'PASS':
                    status_changes = {previous_status: -1, 'PASS': 1}
                    stats_success = self.aws_manager.update_category_status_stats_atomic(
                        main_category, sub_category, status_changes
                    )
                    if stats_success:
                        logger.info(f"상태 통계 업데이트 성공: {main_category}-{sub_category}-{product_id} ({previous_status} -> PASS)")
                    else:
                        logger.warning(f"상태 통계 업데이트 실패: {main_category}-{sub_category}-{product_id}")
                
                # 성공 메시지를 패널 내에서 표시 및 즉시 초기화
                self.show_pass_success_status(product_id)
                
                # 상품 보류 처리 완료 알림 (저장된 product_id 사용)
                self.product_passed.emit(product_id)
            else:
                QMessageBox.warning(self, "오류", "상품 보류 처리에 실패했습니다.")
                
        except Exception as e:
            logger.error(f"상품 보류 처리 중 오류: {e}")
            QMessageBox.critical(self, "오류", f"상품 보류 처리 중 오류가 발생했습니다:\n{str(e)}")
        
        # 성공하지 않은 경우에만 버튼 상태 복원 (성공 시에는 이미 패널이 초기화됨)
        if not success:
            self.pass_btn.setText(original_text)
            self.pass_btn.setEnabled(True)
    
    def show_pass_success_status(self, product_id: str = None):
        """상품 보류 성공 상태를 패널 내에서 시각적으로 표시"""
        try:
            if product_id is None:
                product_id = self.current_product.get('product_id', 'Unknown') if self.current_product else 'Unknown'
            
            # 메인 선택 요약에 보류 메시지 표시
            self.selection_summary.setText(f"⚠️ 상품 보류 완료! 상품 ID: {product_id}")
            self.selection_summary.setStyleSheet("font-weight: bold; color: #856404; background-color: #fff3cd; padding: 8px; border-radius: 4px; border: 1px solid #ffeaa7;")
            
            # 대표 이미지 영역 상태 업데이트
            self.main_status_label.setText("⚠️ 상품이 보류 상태로 처리되었습니다")
            self.main_status_label.setStyleSheet("color: #856404; background-color: #fff3cd; font-size: 11px; padding: 6px; border-radius: 3px; font-weight: bold;")
            
            # 색상 변형 영역 상태 업데이트
            self.color_status_label.setText("⚠️ 보류된 상품입니다 (나중에 다시 처리 가능)")
            self.color_status_label.setStyleSheet("color: #856404; background-color: #fff3cd; font-size: 11px; padding: 6px; border-radius: 3px; font-weight: bold;")
            
            # 버튼들 상태 변경
            self.pass_btn.setText("⚠️ 보류 완료")
            self.pass_btn.setStyleSheet("""
                QPushButton {
                    background-color: #ffc107;
                    color: #212529;
                    border: 2px solid #ffca2c;
                    padding: 8px 16px;
                    border-radius: 4px;
                    font-weight: bold;
                }
            """)
            self.pass_btn.setEnabled(False)
            
            self.complete_btn.setEnabled(False)
            
            # 즉시 패널 초기화
            self._reset_panel_after_completion()
            
        except Exception as e:
            logger.error(f"보류 성공 상태 표시 오류: {str(e)}")
    
    def _reset_panel_after_completion(self):
        """작업 완료 후 패널 초기화"""
        try:
            # 대표 이미지 선택만 초기화 (current_product는 유지)
            self.representative_images = {}
            self.color_variant_images = {}
            
            # 디스플레이 업데이트
            self.update_display()
            
            # 상태 레이블들 초기화
            self.main_status_label.setText("대표 이미지 3개를 선정해주세요")
            self.main_status_label.setStyleSheet("color: #155724; background-color: #d4edda; font-size: 11px; padding: 6px; border-radius: 3px;")
            
            self.color_status_label.setText("대표 이미지 3개를 먼저 선정해주세요")
            self.color_status_label.setStyleSheet("color: #0c4a60; background-color: #d1ecf1; font-size: 11px; padding: 6px; border-radius: 3px;")
            
            # 선택 요약 초기화
            self.selection_summary.setText("선택된 대표 이미지: 0개")
            self.selection_summary.setStyleSheet("font-weight: bold; color: #212529; background-color: transparent; padding-bottom: 10px;")
            
            # 버튼들 초기 상태로 복원
            self.pass_btn.setText("Pass (보류)")
            self.pass_btn.setStyleSheet("""
                QPushButton {
                    background-color: #ffc107;
                    color: #212529;
                    border: none;
                    padding: 8px 16px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #e0a800;
                }
            """)
            self.pass_btn.setEnabled(True)  # 상품이 로드된 상태이므로 활성화
            
            self.complete_btn.setText("큐레이션 완료 (Space)")
            self.complete_btn.setStyleSheet("""
                QPushButton {
                    background-color: #28a745;
                    color: white;
                    border: none;
                    padding: 8px 16px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #218838;
                }
                QPushButton:disabled {
                    background-color: #6c757d;
                }
            """)
            self.complete_btn.setEnabled(False)  # 대표 이미지가 없으므로 비활성화
            
            # 메인 이미지 뷰어의 선택 모드 초기화 (이미지 선택 기능 복원)
            if self.main_image_viewer:
                self.main_image_viewer.clear_selection_mode()
                # 포커스 복원으로 키보드 이벤트 활성화
                self.main_image_viewer.setFocus()
                # 강제로 키보드 이벤트 활성화를 위한 추가 설정
                self.main_image_viewer.setFocusPolicy(Qt.StrongFocus)
            
            logger.info("작업 완료 후 대표 이미지 선택 초기화 완료")
            
        except Exception as e:
            logger.error(f"패널 초기화 오류: {str(e)}")
    
    #CHECK : 중요 함수(큐레이션 완료 처리)
    def complete_curation(self):
        """큐레이션 완료 처리"""
        if not self.current_product:
            QMessageBox.warning(self, "오류", "상품 정보가 없습니다.")
            return
        
        if not self.aws_manager:
            QMessageBox.warning(self, "오류", "AWS 연결이 설정되지 않았습니다.")
            return
        
        # 대표 이미지 수집
        representative_assets = self.collect_representative_assets()
        
        if not representative_assets:
            QMessageBox.warning(self, "대표 이미지 부족", 
                              "대표 이미지를 최소 1개 이상 선택해야 합니다.\n\n"
                              "중앙 이미지 뷰어에서 이미지를 선택하고\n"
                              "우측 클릭 메뉴로 대표 이미지에 추가해주세요.")
            return
        
        # 확인 팝업
        model_count = len(representative_assets.get('model', []))
        product_only_count = len(representative_assets.get('product_only', []))
        color_variant_count = len(representative_assets.get('color_variant', []))
        
        dialog = CurationConfirmDialog(
            self.current_product.get('product_id', 'Unknown'), 
            model_count, 
            product_only_count, 
            color_variant_count, 
            model_count + product_only_count, 
            self
        )
        
        if dialog.exec() == QDialog.Accepted:
            # 초기화되기 전에 필요한 값들을 미리 저장
            product_id = self.current_product.get('product_id', '')
            sub_category = self.current_product.get('sub_category')
            main_category = self.current_product.get('main_category')
            previous_status = self.current_product.get('current_status', 'PENDING')
            
            # 버튼 상태 변경
            original_text = self.complete_btn.text()
            self.complete_btn.setText("🔄 처리 중...")
            self.complete_btn.setEnabled(False)
            
            success = False
            
            try:
                # 1단계: 로컬 segment 이미지들을 S3에 업로드
                logger.info("로컬 segment 이미지 S3 업로드 시작")
                
                # 업로드할 로컬 이미지 개수 확인
                local_images_count = 0
                for image_type, image_data in self.representative_images.items():
                    if self._is_local_segment_image(image_data):
                        local_images_count += 1
                for image_key, image_data in self.color_variant_images.items():
                    if self._is_local_segment_image(image_data):
                        local_images_count += 1
                
                if local_images_count > 0:
                    # 진행 상황 메시지 표시
                    self.selection_summary.setText(f"🔄 로컬 segment 이미지 {local_images_count}개를 S3에 업로드 중...")
                    self.selection_summary.setStyleSheet("font-weight: bold; color: #007bff; background-color: transparent; padding-bottom: 10px;")
                    
                    # UI 업데이트를 위해 이벤트 루프 처리
                    QCoreApplication.processEvents()
                
                upload_success = self.upload_local_segment_images_to_s3(representative_assets)
                
                if not upload_success:
                    QMessageBox.warning(self, "업로드 실패", "로컬 segment 이미지 S3 업로드에 실패했습니다.")
                    return
                
                logger.info("로컬 segment 이미지 S3 업로드 완료")
                
                # 2단계: MainImageViewer에서 대기 중인 S3 이동 작업 처리
                moved_filenames = []
                if self.main_image_viewer:
                    pending_moves = self.main_image_viewer.get_pending_moves()
                    
                    if pending_moves:
                        logger.info(f"대기 중인 S3 이동 작업 {len(pending_moves)}개 처리 시작")
                        
                        # 진행 상황 메시지 표시
                        self.selection_summary.setText(f"🔄 {len(pending_moves)}개 이미지를 S3에서 segment → text 폴더로 이동 중...")
                        self.selection_summary.setStyleSheet("font-weight: bold; color: #007bff; background-color: transparent; padding-bottom: 10px;")
                        QCoreApplication.processEvents()
                        
                        # S3 이동 작업 실행
                        move_results = self.aws_manager.batch_move_s3_objects(pending_moves)
                        
                        # 성공한 이동 작업에서 파일명 추출
                        # move_results는 {source_key: success} 형태로 반환됨
                        # pending_moves는 [(source_key, dest_key), ...] 형태
                        for source_key, dest_key in pending_moves:
                            success = move_results.get(source_key, False)
                            if success:
                                # dest_key에서 파일명 추출 (예: "category/sub/product/text/filename.jpg" -> "filename.jpg")
                                filename = dest_key.split('/')[-1]
                                moved_filenames.append(filename)
                                logger.info(f"S3 이동 성공: {source_key} -> {dest_key}")
                            else:
                                logger.error(f"S3 이동 실패: {source_key} -> {dest_key}")
                        
                        # pending_moves 정리
                        self.main_image_viewer.clear_pending_moves()
                        
                        logger.info(f"S3 이동 완료: {len(moved_filenames)}개 파일")
                
                # 3단계: DynamoDB에 큐레이션 결과 저장
                logger.info("DynamoDB 큐레이션 결과 저장 시작")
                
                # 제외할 text 파일명 수집
                excluded_text_filenames = []
                if self.main_image_viewer:
                    excluded_text_filenames = self.main_image_viewer.get_excluded_text_filenames()
                    if excluded_text_filenames:
                        logger.info(f"DynamoDB에서 제외할 text 파일: {excluded_text_filenames}")
                
                success = self.aws_manager.update_curation_result(
                    sub_category=sub_category,
                    product_id=product_id,
                    representative_images=self.representative_images,
                    color_variant_images=self.color_variant_images,
                    excluded_text_filenames=excluded_text_filenames
                )
                
                if not success:
                    QMessageBox.warning(self, "오류", "큐레이션 결과 저장에 실패했습니다.")
                    return
                
                # 4단계: text 폴더로 이동된 파일들을 DynamoDB text 필드에 추가 및 segment 필드에서 제거
                if moved_filenames:
                    logger.info(f"DynamoDB 필드 업데이트 시작: {len(moved_filenames)}개 파일")
                    
                    # 진행 상황 메시지 표시
                    self.selection_summary.setText(f"🔄 DynamoDB에 이동된 {len(moved_filenames)}개 파일 정보 업데이트 중...")
                    self.selection_summary.setStyleSheet("font-weight: bold; color: #007bff; background-color: transparent; padding-bottom: 10px;")
                    QCoreApplication.processEvents()
                    
                    # text 필드에 파일명들 추가
                    text_update_success = self.aws_manager.append_files_to_text_field(
                        sub_category=sub_category,
                        product_id=product_id,
                        filenames=moved_filenames
                    )
                    
                    if text_update_success:
                        logger.info(f"DynamoDB text 필드 업데이트 성공: {moved_filenames}")
                    else:
                        logger.warning(f"DynamoDB text 필드 업데이트 실패: {moved_filenames}")
                        # text 필드 업데이트 실패는 치명적이지 않으므로 계속 진행
                    
                    # segment 필드에서 이동된 파일명들 제거
                    segment_update_success = self.aws_manager.remove_files_from_segment_field(
                        sub_category=sub_category,
                        product_id=product_id,
                        filenames=moved_filenames
                    )
                    
                    if segment_update_success:
                        logger.info(f"DynamoDB segment 필드 업데이트 성공: {moved_filenames}")
                    else:
                        logger.warning(f"DynamoDB segment 필드 업데이트 실패: {moved_filenames}")
                        # segment 필드 업데이트 실패는 치명적이지 않으므로 계속 진행
                
                # 5단계: 상태 통계 업데이트
                if previous_status != 'COMPLETED':
                    status_changes = {previous_status: -1, 'COMPLETED': 1}
                    stats_success = self.aws_manager.update_category_status_stats_atomic(
                        main_category, sub_category, status_changes
                    )
                    if stats_success:
                        logger.info(f"상태 통계 업데이트 성공: {main_category}-{sub_category}-{product_id} ({previous_status} -> COMPLETED)")
                    else:
                        logger.warning(f"상태 통계 업데이트 실패: {main_category}-{sub_category}-{product_id}")
                
                # 성공 메시지를 패널 내에서 표시 및 즉시 초기화
                self.show_complete_success_status(product_id)
                
                # 큐레이션 완료 알림 (저장된 product_id 사용)
                self.curation_completed.emit(product_id)
                    
            except Exception as e:
                logger.error(f"큐레이션 완료 처리 중 오류: {e}")
                QMessageBox.critical(self, "오류", f"큐레이션 완료 처리 중 오류가 발생했습니다:\n{str(e)}")
            
            # 성공하지 않은 경우에만 버튼 상태 복원 (성공 시에는 이미 패널이 초기화됨)
            if not success:
                self.complete_btn.setText(original_text)
                self.complete_btn.setEnabled(True)
    
    def collect_representative_assets(self):
        """대표 이미지 수집"""
        # 모델 착용 이미지 수집
        model_images = []
        if 'model_wearing' in self.representative_images:
            model_images.append(self.representative_images['model_wearing'])
        
        # 제품 단독 이미지 수집 (정면 누끼, 후면 누끼 + 색상 변형)
        product_only_images = []
        if 'front_cutout' in self.representative_images:
            product_only_images.append(self.representative_images['front_cutout'])
        if 'back_cutout' in self.representative_images:
            product_only_images.append(self.representative_images['back_cutout'])
        
        # 색상 변형 이미지들도 제품 단독에 포함
        product_only_images.extend(list(self.color_variant_images.values()))
        
        assets = {
            'model': model_images,
            'product_only': product_only_images,
            'color_variant': list(self.color_variant_images.values())  # 호환성을 위해 유지
        }
        return assets
    
    def upload_local_segment_images_to_s3(self, curation_data: dict) -> bool:
        """로컬 segment 이미지들을 S3에 업로드"""
        if not self.current_product or not self.aws_manager:
            logger.error("상품 정보 또는 AWS 매니저가 없습니다.")
            return False
        
        try:
            # 상품 정보 추출
            main_category = self.current_product.get('main_category', '')
            sub_category = self.current_product.get('sub_category', '')
            product_id = self.current_product.get('product_id', '')
            
            logger.info(f"상품 정보: main_category={main_category}, sub_category={sub_category}, product_id={product_id}")
            
            if not all([main_category, sub_category, product_id]):
                logger.error("상품 정보가 불완전합니다.")
                return False
            
            # 업로드할 로컬 이미지들 수집
            local_images_to_upload = []
            
            # 대표 이미지들에서 로컬 segment 이미지 찾기
            for image_type, image_data in self.representative_images.items():
                logger.debug(f"대표 이미지 검사: {image_type} - is_local_segment={image_data.get('is_local_segment', False)}")
                if self._is_local_segment_image(image_data):
                    local_images_to_upload.append(image_data)
                    logger.info(f"로컬 segment 이미지 발견 (대표): {image_data.get('filename', 'unknown')}")
            
            # 색상 변형 이미지들에서 로컬 segment 이미지 찾기
            for image_key, image_data in self.color_variant_images.items():
                logger.debug(f"색상 변형 이미지 검사: {image_key} - is_local_segment={image_data.get('is_local_segment', False)}")
                if self._is_local_segment_image(image_data):
                    local_images_to_upload.append(image_data)
                    logger.info(f"로컬 segment 이미지 발견 (색상 변형): {image_data.get('filename', 'unknown')}")
            
            if not local_images_to_upload:
                logger.info("업로드할 로컬 segment 이미지가 없습니다.")
                return True
            
            logger.info(f"S3에 업로드할 로컬 segment 이미지 {len(local_images_to_upload)}개 발견")
            
            # 각 로컬 이미지를 S3에 업로드
            upload_success_count = 0
            for i, image_data in enumerate(local_images_to_upload, 1):
                logger.info(f"업로드 진행 중 ({i}/{len(local_images_to_upload)}): {image_data.get('filename', 'unknown')}")
                if self._upload_single_local_image_to_s3(image_data, main_category, sub_category, product_id):
                    upload_success_count += 1
                    logger.info(f"로컬 이미지 S3 업로드 성공: {image_data.get('filename', 'unknown')}")
                else:
                    logger.error(f"로컬 이미지 S3 업로드 실패: {image_data.get('filename', 'unknown')}")
            
            logger.info(f"로컬 segment 이미지 S3 업로드 완료: {upload_success_count}/{len(local_images_to_upload)}")
            return upload_success_count == len(local_images_to_upload)
            
        except Exception as e:
            logger.error(f"로컬 segment 이미지 S3 업로드 중 오류: {str(e)}")
            return False
    
    def _is_local_segment_image(self, image_data: dict) -> bool:
        """이미지가 로컬에서 생성된 segment 이미지인지 확인"""
        return (image_data.get('is_local_segment', False) and 
                image_data.get('local_path') and 
                os.path.exists(image_data.get('local_path')))
    
    def _upload_single_local_image_to_s3(self, image_data: dict, main_category: str, sub_category: str, product_id: str) -> bool:
        """단일 로컬 이미지를 S3에 업로드"""
        try:
            local_path = image_data.get('local_path')
            filename = image_data.get('filename', os.path.basename(local_path))
            
            logger.info(f"업로드 시작: {filename}")
            logger.info(f"로컬 경로: {local_path}")
            
            if not local_path or not os.path.exists(local_path):
                logger.error(f"로컬 파일이 존재하지 않습니다: {local_path}")
                return False
            
            # 파일 크기 확인
            file_size = os.path.getsize(local_path)
            logger.info(f"파일 크기: {file_size} bytes")
            
            # S3 키 생성
            # sub_category를 int로 변환 (문자열인 경우)
            try:
                sub_category_int = int(sub_category) if isinstance(sub_category, str) else sub_category
                logger.info(f"sub_category 변환: {sub_category} -> {sub_category_int}")
            except (ValueError, TypeError):
                logger.error(f"sub_category를 int로 변환할 수 없습니다: {sub_category}")
                return False
            
            s3_key = self.aws_manager._get_s3_object_key(
                main_category=main_category,
                sub_category=sub_category_int,
                product_id=product_id,
                relative_path=f"segment/{filename}"
            )
            
            logger.info(f"생성된 S3 키: {s3_key}")
            
            # 메타데이터 준비
            metadata = {
                'created_from': image_data.get('created_from', ''),
                'original_url': image_data.get('original_url', ''),
                'segment_info': str(image_data.get('segment_info', {})),
                'uploaded_by': 'curation_tool',
                'upload_timestamp': self.aws_manager._get_current_timestamp()
            }
            
            logger.info(f"메타데이터: {metadata}")
            
            # S3에 업로드
            logger.info(f"S3 업로드 시작: {local_path} -> {s3_key}")
            success = self.aws_manager.upload_file_to_s3(
                local_file_path=local_path,
                s3_key=s3_key,
                metadata=metadata
            )
            
            if success:
                # 업로드 성공 시 이미지 데이터 업데이트
                image_data['url'] = f"s3://{self.aws_manager.bucket_name}/{s3_key}"
                image_data['s3_key'] = s3_key
                image_data['is_local_segment'] = False  # 이제 S3에 있음
                image_data['uploaded_to_s3'] = True
                logger.info(f"이미지 데이터 업데이트 완료: {filename}")
                logger.info(f"새로운 URL: {image_data['url']}")
            else:
                logger.error(f"S3 업로드 실패: {filename}")
            
            return success
            
        except Exception as e:
            logger.error(f"단일 로컬 이미지 S3 업로드 중 오류: {str(e)}")
            return False
    
    def clear(self):
        """패널 초기화"""
        self.current_product = None
        self.representative_images = {}
        self.color_variant_images = {}
        self.product_info_label.setText("상품을 선택해주세요")
        
        # 버튼들 초기 상태로 복원
        # Pass 버튼 초기화
        self.pass_btn.setText("Pass (보류)")
        self.pass_btn.setStyleSheet("""
            QPushButton {
                background-color: #ffc107;
                color: #212529;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #e0a800;
            }
        """)
        self.pass_btn.setEnabled(False)  # 상품이 로드되기 전까지는 비활성화
        
        # 완료 버튼 초기화
        self.complete_btn.setText("큐레이션 완료 (Space)")
        self.complete_btn.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #218838;
            }
            QPushButton:disabled {
                background-color: #6c757d;
            }
        """)
        self.complete_btn.setEnabled(False)
        
        # 상태 레이블들 초기화
        self.main_status_label.setText("대표 이미지 3개를 선정해주세요")
        self.main_status_label.setStyleSheet("color: #155724; background-color: #d4edda; font-size: 11px; padding: 6px; border-radius: 3px;")
        
        self.color_status_label.setText("대표 이미지 3개를 먼저 선정해주세요")
        self.color_status_label.setStyleSheet("color: #0c4a60; background-color: #d1ecf1; font-size: 11px; padding: 6px; border-radius: 3px;")
        
        # 선택 요약 초기화
        self.selection_summary.setText("선택된 대표 이미지: 0개")
        self.selection_summary.setStyleSheet("font-weight: bold; color: #212529; background-color: transparent; padding-bottom: 10px;")
        
        # 디스플레이 업데이트
        self.update_display()
        
        logger.info("대표 이미지 패널 초기화 완료")
    
    def cleanup(self):
        """위젯 정리 - 메모리 누수 방지"""
        try:
            self._is_destroyed = True
            
            # 데이터 초기화
            self.current_product = None
            self.representative_images = {}
            self.color_variant_images = {}
            
            # 레이아웃 정리
            self.clear_layout(self.main_rep_grid_layout)
            self.clear_layout(self.color_grid_layout)
            
            # 스레드 정리
            if hasattr(self, 'curation_worker') and self.curation_worker:
                if self.curation_worker.isRunning():
                    self.curation_worker.quit()
                    if not self.curation_worker.wait(3000):  # 3초 대기
                        self.curation_worker.terminate()  # 강제 종료
                        self.curation_worker.wait()
                self.curation_worker.deleteLater()
                self.curation_worker = None
            
            # 참조 정리
            self.aws_manager = None
            self.image_cache = None
            self.main_image_viewer = None
            
            logger.info("RepresentativePanel 정리 완료")
            
        except Exception as e:
            logger.warning(f"RepresentativePanel 정리 중 오류: {str(e)}") 