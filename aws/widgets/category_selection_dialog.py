#!/usr/bin/env python3
"""
카테고리 선택 다이얼로그
사용자가 main 카테고리와 sub 카테고리를 선택할 수 있는 다이얼로그
"""

import json
from typing import Optional, Dict, List
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                               QComboBox, QPushButton, QGroupBox, QMessageBox,
                               QTextEdit, QSplitter)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from aws_manager import AWSManager
from dataclasses import dataclass , field
from typing import Annotated
import logging
logger = logging.getLogger(__name__)
@dataclass
class CategoryStats:
    total: int
    completed: int
    passed: int
    pending: int

@dataclass
class CategoryInfo:
    """
    카테고리 정보 클래스
        example : 
        {
            "categories": {
                "TOP": {1005: CategoryStats(total=150, completed=25, passed=5, pending=120),
                        1006: CategoryStats(total=150, completed=25, passed=5, pending=120)},
                "BOTTOM": {1007: CategoryStats(total=150, completed=25, passed=5, pending=120),
                           1008: CategoryStats(total=150, completed=25, passed=5, pending=120)}
            },
            "total_products": 300
        }
    """
    categories: Dict[str, Dict[int, CategoryStats]] = field(default_factory=dict)
    total_products: int = 0

class CategorySelectionDialog(QDialog):
    """카테고리 선택 다이얼로그"""
    
    category_selected:Signal = Signal(str, int) 
    """category_selected 시그널: 카테고리 선택 완료 시 데이터 emit => (str, int) : (main_category, sub_category)"""

    
    def __init__(self, aws_manager:AWSManager, parent=None):
        super().__init__(parent)
        self.aws_manager = aws_manager
        self.selected_main_category = None
        self.selected_sub_category = None
        self.categories_info = None
        
        self.setup_ui()
        self.load_categories()
    
    def setup_ui(self):
        """UI 설정"""
        self.setWindowTitle("카테고리 선택")
        self.setModal(True)
        self.resize(600, 500)
        
        # 메인 레이아웃
        main_layout = QVBoxLayout(self)
        
        # 제목
        title_label = QLabel("큐레이션할 카테고리를 선택해주세요")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)
        
        # 스플리터로 좌우 분할
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)
        
        # 왼쪽: 카테고리 선택
        self.setup_selection_panel(splitter)
        
        # 오른쪽: 정보 표시
        self.setup_info_panel(splitter)
        
        # 스플리터 비율 설정
        splitter.setSizes([300, 300])
        
        # 버튼 영역
        self.setup_button_area(main_layout)
    
    def setup_selection_panel(self, parent):
        """카테고리 선택 패널 설정"""
        selection_group = QGroupBox("카테고리 선택")
        selection_layout = QVBoxLayout(selection_group)
        
        # 메인 카테고리 선택
        main_category_layout = QHBoxLayout()
        main_category_layout.addWidget(QLabel("메인 카테고리:"))
        
        self.main_category_combo = QComboBox()
        self.main_category_combo.currentTextChanged.connect(self.on_main_category_changed)
        main_category_layout.addWidget(self.main_category_combo)
        
        selection_layout.addLayout(main_category_layout)
        
        # 서브 카테고리 선택
        sub_category_layout = QHBoxLayout()
        sub_category_layout.addWidget(QLabel("서브 카테고리:"))
        
        self.sub_category_combo = QComboBox()
        self.sub_category_combo.currentTextChanged.connect(self.on_sub_category_changed)
        sub_category_layout.addWidget(self.sub_category_combo)
        
        selection_layout.addLayout(sub_category_layout)
        
        # 선택된 카테고리 정보
        self.selection_info_label = QLabel("카테고리를 선택하세요")
        self.selection_info_label.setWordWrap(True)
        self.selection_info_label.setStyleSheet("padding: 10px; background-color: #f8f9fa; color: #495057; border: 1px solid #dee2e6; border-radius: 5px;")
        selection_layout.addWidget(self.selection_info_label)
        
        selection_layout.addStretch()
        parent.addWidget(selection_group)
    
    def setup_info_panel(self, parent):
        """정보 표시 패널 설정"""
        info_group = QGroupBox("카테고리 정보")
        info_layout = QVBoxLayout(info_group)
        
        # 전체 통계 정보
        self.stats_label = QLabel("통계 정보를 로딩 중...")
        self.stats_label.setWordWrap(True)
        info_layout.addWidget(self.stats_label)
        
        # 상세 정보 (JSON 형태)
        detail_label = QLabel("상세 정보:")
        info_layout.addWidget(detail_label)
        
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setMaximumHeight(200)
        info_layout.addWidget(self.detail_text)
        
        parent.addWidget(info_group)
    
    def setup_button_area(self, parent_layout):
        """버튼 영역 설정"""
        button_layout = QHBoxLayout()
        
        # 새로고침 버튼
        refresh_button = QPushButton("새로고침")
        refresh_button.clicked.connect(self.load_categories)
        button_layout.addWidget(refresh_button)
        
        button_layout.addStretch()
        
        # 취소 버튼
        cancel_button = QPushButton("취소")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)
        
        # 확인 버튼
        self.ok_button = QPushButton("확인")
        self.ok_button.clicked.connect(self.accept_selection)
        self.ok_button.setEnabled(False)
        self.ok_button.setDefault(True)
        button_layout.addWidget(self.ok_button)
        
        parent_layout.addLayout(button_layout)
    
    def load_categories(self):
        """
        카테고리 정보 로드 - 새로운 상태 통계 시스템 사용
            - 카테고리 정보는 DynamoDB 상태 통계에서 조회(맨 처음 다이얼로그 실행 시, 새로고침 버튼 클릭 시)
        """
        try:
            # DynamoDB에서 모든 카테고리 상태 통계 조회
            all_stats = self.aws_manager.get_all_category_status_stats()
            
            if not all_stats:
                QMessageBox.warning(self, "카테고리 정보 없음", 
                                  "카테고리 상태 통계가 없습니다.\n초기 데이터 업로드를 먼저 실행해주세요.")
                return
            
            # 상태 통계에서 카테고리 정보 구성
            categories_info = self.build_categories_info_from_stats(all_stats)
            self.categories_info = categories_info
            
            # 메인 카테고리 콤보박스 업데이트
            self.main_category_combo.clear()
            self.main_category_combo.addItem("-- 선택하세요 --", None)
            
            main_categories = categories_info.categories.keys()
            for main_category in main_categories:
                self.main_category_combo.addItem(main_category, main_category)
            
            # 통계 정보 업데이트
            self.update_stats_info()
            
           
            
        except Exception as e:
            QMessageBox.critical(self, "오류", f"카테고리 정보 로드 중 오류가 발생했습니다:\n{str(e)}")
    
    def build_categories_info_from_stats(self, all_stats: dict) -> CategoryInfo:
        """
        상태 통계에서 카테고리 정보를 구성합니다.
        
        Args:
            all_stats: get_all_category_status_stats() 결과
                example : 
                {
                    'TOP_1005': {
                        'pending': 120,
                        'completed': 25,
                        'pass': 5,
                        'total': 150
                    }
                }
            
        Returns:
            CategoryInfo: 카테고리별 통계 정보를 포함하는 객체
        """
        result = CategoryInfo()
        
        # 상태 통계에서 카테고리 정보 추출
        for category_key, stats in all_stats.items():
            try:
                main_category, sub_category_str = category_key.split('_')
                sub_category = int(sub_category_str)
                
                if main_category not in result.categories:
                    result.categories[main_category] = {}
                
                category_stats = CategoryStats(
                    total=stats.get('total', 0),
                    completed=stats.get('completed', 0),
                    passed=stats.get('pass', 0),
                    pending=stats.get('pending', 0)
                )
                
                result.categories[main_category][sub_category] = category_stats
                result.total_products += category_stats.total
                
            except (ValueError, IndexError) as e:
                logger.error(f"카테고리 키 파싱 오류: {category_key} - {e}")
                continue
        
        return result
    
    @Slot(str)
    def on_main_category_changed(self, main_category_text:str):
        """메인 카테고리 변경 시 처리"""
        self.sub_category_combo.clear()
        self.sub_category_combo.addItem("-- 선택하세요 --", None)
        
        main_category = self.main_category_combo.currentData()
        if main_category and self.categories_info:
            # 서브 카테고리 목록 업데이트
            sub_categories = self.categories_info.categories[main_category].keys()
            
            for sub_category in sub_categories:
                # 제품 수 정보도 함께 표시
                product_count = self.categories_info.categories[main_category][sub_category].total
                completed_count = self.categories_info.categories[main_category][sub_category].completed
                passed_count = self.categories_info.categories[main_category][sub_category].passed
                pending_count = self.categories_info.categories[main_category][sub_category].pending
                display_text = f"{sub_category} ({product_count}개 제품) (미정: {pending_count:,}, 완료: {completed_count:,}, 보류: {passed_count:,})"
                self.sub_category_combo.addItem(display_text, sub_category)
        
        self.selected_main_category = main_category
        self.update_selection_info()
        self.check_selection_complete()
    
    @Slot(str)
    def on_sub_category_changed(self, sub_category_text:str):
        """서브 카테고리 변경 시 처리"""
        self.selected_sub_category = self.sub_category_combo.currentData()
        self.update_selection_info()
        self.check_selection_complete()
    
    def update_selection_info(self):
        """선택 정보 업데이트"""
        if self.selected_main_category and self.selected_sub_category:
            # 선택된 카테고리의 제품 수 정보
            if self.categories_info:
                selected_info = self.categories_info.categories[self.selected_main_category][self.selected_sub_category]
            
            info_text = f"""
                선택된 카테고리:
                • 메인 카테고리: {self.selected_main_category}
                • 서브 카테고리: {self.selected_sub_category}
                • 제품 수: {selected_info.total:,}개
                    • 미정: {selected_info.pending:,}개
                    • 완료: {selected_info.completed:,}개
                    • 보류: {selected_info.passed:,}개

                이 카테고리로 큐레이션 작업을 시작하겠습니다.
            """.strip()
            
            self.selection_info_label.setText(info_text)
            self.selection_info_label.setStyleSheet("padding: 10px; background-color: #d4edda; border: 1px solid #c3e6cb; border-radius: 5px; color: #155724;")
        
        elif self.selected_main_category:
            self.selection_info_label.setText(f"메인 카테고리 '{self.selected_main_category}'가 선택되었습니다.\n서브 카테고리를 선택해주세요.")
            self.selection_info_label.setStyleSheet("padding: 10px; background-color: #fff3cd; border: 1px solid #ffeaa7; border-radius: 5px; color: #856404;")
        
        else:
            self.selection_info_label.setText("카테고리를 선택해주세요")
            self.selection_info_label.setStyleSheet("padding: 10px; background-color: #f8f9fa; color: #495057; border: 1px solid #dee2e6; border-radius: 5px;")
    
    def update_stats_info(self):
        """통계 정보 업데이트 - 새로운 상태 통계 시스템 사용"""
        if not self.categories_info:
            self.stats_label.setText("카테고리 정보가 없습니다.")
            return
        
        try:
            # 전체 통계 계산
            total_products = self.categories_info.total_products
            main_categories = self.categories_info.categories.keys()
            
            # 전체 상태별 통계 계산
            total_pending = 0
            total_completed = 0
            total_passed = 0
            
            # 카테고리별 통계 문자열 생성
            category_stats_lines = []
            
            for main_cat, sub_cats in self.categories_info.categories.items():
                # 메인 카테고리별 상태 통계 계산
                cat_total = 0
                cat_pending = 0
                cat_completed = 0
                cat_passed = 0
                
                # 서브 카테고리 통계 라인들
                sub_cat_lines = []
                
                for sub_cat, stats in sub_cats.items():
                    # 메인 카테고리 합계에 더하기
                    cat_total += stats.total
                    cat_pending += stats.pending
                    cat_completed += stats.completed
                    cat_passed += stats.passed
                    
                    # 전체 통계에 더하기
                    total_pending += stats.pending
                    total_completed += stats.completed
                    total_passed += stats.passed
                    
                    # 서브 카테고리 통계 라인 추가
                    sub_cat_lines.append(
                        f"    └ {sub_cat}: {stats.total:,}개 (미정: {stats.pending:,}, 완료: {stats.completed:,}, 보류: {stats.passed:,})"
                    )
                
                # 메인 카테고리 통계 라인 추가
                category_stats_lines.append(
                    f"• {main_cat}: {cat_total:,}개 (미정: {cat_pending:,}, 완료: {cat_completed:,}, 보류: {cat_passed:,})"
                )
                # 서브 카테고리 라인들 추가
                category_stats_lines.extend(sub_cat_lines)
            
            # 전체 통계 문자열 생성
            stats_text = "전체 통계:\n"
            stats_text += f"• 총 제품 수: {total_products:,}개\n"
            stats_text += f"• 메인 카테고리 수: {len(main_categories)}개\n"
            stats_text += f"• 미정: {total_pending:,}개\n"
            stats_text += f"• 완료: {total_completed:,}개\n"
            stats_text += f"• 보류: {total_passed:,}개\n\n"
            
            # # 카테고리별 통계 추가
            # stats_text += "카테고리별 제품 수:\n"
            detail_text = "\n".join(category_stats_lines)
            
            self.stats_label.setText(stats_text)
            self.detail_text.setPlainText(detail_text)
            
        except Exception as e:
            self.stats_label.setText(f"통계 정보 로드 오류: {str(e)}")
            logger.error(f"통계 정보 업데이트 오류: {e}")
    
    
    def check_selection_complete(self):
        """선택 완료 여부 확인"""
        is_complete = bool(self.selected_main_category and self.selected_sub_category)
        self.ok_button.setEnabled(is_complete)
    
    @Slot()
    def accept_selection(self):
        """맨 마지막 버튼 클릭시 동작하는 slot 함수"""
        if self.selected_main_category and self.selected_sub_category:
            # 선택 완료 시그널 발생
            self.category_selected.emit(self.selected_main_category, self.selected_sub_category)
            self.accept()
        else:
            QMessageBox.warning(self, "선택 필요", "메인 카테고리와 서브 카테고리를 모두 선택해주세요.") 