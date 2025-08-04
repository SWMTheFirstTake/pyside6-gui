#!/usr/bin/env python3
"""
메인 이미지 뷰어 위젯
선택된 이미지를 중앙에 크게 표시합니다.
"""

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                               QPushButton, QScrollArea, QFrame, QButtonGroup,
                               QComboBox, QTabWidget, QGridLayout, QSpacerItem, QSizePolicy, QDialog, QTextEdit, QDialogButtonBox)
from PySide6.QtCore import Qt, Signal, QSize, QTimer, QThread
from PySide6.QtGui import QPixmap, QFont, QColor, QPainter, QPen, QIcon, QKeyEvent, QShortcut, QKeySequence
from typing import Dict, Any, List, Optional
import logging
import json
import os
from PIL import Image
from pathlib import Path
import weakref
import sys
import webbrowser  # 무신사 페이지 열기 위한 모듈 추가
from typing import Dict, Any, List, Optional, Tuple
from functools import partial
from datetime import datetime

# 이미지 뷰어 모듈 import
from .image_viewer_dialog import UrlImageViewerDialog

logger = logging.getLogger(__name__)


class MetaJsonDialog(QDialog):
    """meta.json 파일 내용을 표시하는 팝업 다이얼로그"""
    
    def __init__(self, meta_data: Dict[str, Any], product_id: str, parent=None):
        super().__init__(parent)
        self.meta_data = meta_data
        self.product_id = product_id
        self.setup_ui()
    
    def setup_ui(self):
        """UI 설정"""
        self.setWindowTitle(f"Product Meta Info - {self.product_id}")
        self.resize(500, 400)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 제목
        title_label = QLabel(f"📋 상품 메타 정보")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(16)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #2c3e50; margin-bottom: 10px;")
        layout.addWidget(title_label)
        
        # 상품 ID 표시
        product_info_label = QLabel(f"Product ID: {self.product_id}")
        product_info_label.setStyleSheet("color: #7f8c8d; font-size: 12px; margin-bottom: 10px;")
        product_info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(product_info_label)
        
        # JSON 내용을 표시
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setStyleSheet("""
            QTextEdit {
                font-size: 11px;
                color: #212529;
                background-color: #ffffff;
                border: 1px solid #dee2e6;
                border-radius: 5px;
                padding: 10px;
            }
        """)
        
        # JSON을 예쁘게 포맷팅
        formatted_json = json.dumps(self.meta_data, indent=2, ensure_ascii=False)
        text_edit.setPlainText(formatted_json)
        
        layout.addWidget(text_edit)
        
        # 버튼
        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        button_box.setStyleSheet("""
            QDialogButtonBox QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
                min-width: 80px;
            }
            QDialogButtonBox QPushButton:hover {
                background-color: #2980b9;
            }
        """)
        layout.addWidget(button_box)



class CurationWorker(QThread):
    """큐레이션 완료 처리를 위한 워커 쓰레드"""
    
    progress_updated = Signal(str, int)  # 상태 메시지, 진행률
    completed = Signal(bool, str)  # 성공 여부, 메시지
    
    def __init__(self, aws_manager, move_operations: list):
        super().__init__()
        self.aws_manager = aws_manager
        self.move_operations = move_operations.copy()  # 복사본 사용
        self._is_cancelled = False  # 취소 플래그
    
    def cancel(self):
        """작업 취소"""
        self._is_cancelled = True
    
    def run(self):
        """백그라운드에서 S3 이동 작업 수행"""
        try:
            total_operations = len(self.move_operations)
            
            if total_operations == 0:
                self.completed.emit(True, "이동할 이미지가 없습니다.")
                return
            
            self.progress_updated.emit("S3 이미지 이동을 시작합니다...", 0)
            
            # 취소 확인
            if self._is_cancelled:
                self.completed.emit(False, "작업이 취소되었습니다.")
                return
            
            # 배치 이동 실행
            results = self.aws_manager.batch_move_s3_objects(self.move_operations)
            
            # 취소 확인
            if self._is_cancelled:
                self.completed.emit(False, "작업이 취소되었습니다.")
                return
            
            # 결과 분석
            success_count = sum(1 for success in results.values() if success)
            failed_operations = [key for key, success in results.items() if not success]
            
            if success_count == total_operations:
                self.progress_updated.emit("모든 이미지 이동 완료", 100)
                self.completed.emit(True, f"✅ {success_count}개 이미지가 성공적으로 이동되었습니다.")
            else:
                failed_count = total_operations - success_count
                message = f"⚠️ {success_count}/{total_operations}개 이미지 이동 완료. {failed_count}개 실패."
                if failed_operations:
                    message += f"\n실패한 이미지: {', '.join(failed_operations[:3])}"
                    if len(failed_operations) > 3:
                        message += f" 외 {len(failed_operations) - 3}개"
                
                self.completed.emit(False, message)
                
        except Exception as e:
            if not self._is_cancelled:
                self.completed.emit(False, f"❌ 큐레이션 처리 중 오류: {str(e)}")
            else:
                self.completed.emit(False, "작업이 취소되었습니다.")
        finally:
            # 메모리 정리
            self.move_operations.clear()


class GridImageWidget(QWidget):
    """중앙 패널에서 하나의 이미지 객체를 담당하는 위젯"""
    
    clicked = Signal(dict)  # 이미지 데이터
    double_clicked = Signal(dict)  # 더블클릭된 이미지 데이터
    
    def __init__(self, image_data: Dict[str, Any], image_cache=None):
        super().__init__()
        self.image_data = image_data
        self.image_cache = image_cache
        self.is_selected = False
        self.is_excluded_from_dynamodb = False  # DynamoDB 제외 상태 추가
        self._is_destroyed = False  # 위젯 파괴 상태 추적
        self._signals_connected = False  # 시그널 연결 상태 추적
        self._connected_signals_and_slots = []  # (시그널, 슬롯) 튜플 저장
        self._cleanup_timer = None  # 정리 타이머
        
        self.setup_ui()
        self.load_image()
    
    def setup_ui(self):
        """UI 설정"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)
        
        # 이미지 표시 프레임
        self.image_frame = QFrame()
        self.image_frame.setFrameStyle(QFrame.Box)
        self.image_frame.setLineWidth(2)
        self.update_frame_style()
        
        frame_layout = QVBoxLayout(self.image_frame)
        frame_layout.setContentsMargins(5, 5, 5, 5)
        
        # 이미지 레이블 - 고정 크기로 설정하되 scaleContents는 사용하지 않음
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setFixedSize(200, 200)
        self.image_label.setStyleSheet("""
            QLabel {
                background-color: white; 
                border: 1px solid #ddd;
                border-radius: 3px;
            }
        """)
        # scaleContents를 사용하지 않고 직접 스케일링 처리
        
        frame_layout.addWidget(self.image_label)
        layout.addWidget(self.image_frame)
        
        # 파일명 레이블
        filename = self.image_data.get('filename', self.image_data.get('url', '').split('/')[-1])
        
        # Segment 이미지인 경우 친숙한 표시명 사용
        if self.image_data.get('is_local_segment', False):
            display_name = self.image_data.get('display_name', filename)
            # 표시명이 너무 길면 줄임
            if len(display_name) > 25:
                filename = display_name[:22] + "..."
            else:
                filename = display_name
        else:
            # 일반 이미지는 기존 방식
            if len(filename) > 20:
                filename = filename[:17] + "..."
        
        self.filename_label = QLabel(filename)
        self.filename_label.setAlignment(Qt.AlignCenter)
        self.filename_label.setWordWrap(True)
        self.filename_label.setStyleSheet("font-size: 9px; color: #333; background-color: white; max-height: 30px; padding: 2px;")
        layout.addWidget(self.filename_label)
        
        # 마우스 이벤트 설정 - 더 안전한 방식으로 처리
        self.setMouseTracking(True)
        self.image_frame.mousePressEvent = self.on_clicked
        self.image_label.mousePressEvent = self.on_clicked
        self.image_frame.mouseDoubleClickEvent = self.on_double_clicked
        self.image_label.mouseDoubleClickEvent = self.on_double_clicked

    def closeEvent(self, event):
        """위젯 닫힐 때 호출"""
        self._is_destroyed = True
        self.schedule_cleanup()
        super().closeEvent(event)

    def deleteLater(self):
        """위젯 삭제 예정 시 호출"""
        self._is_destroyed = True
        self.schedule_cleanup()
        super().deleteLater()
    
    def schedule_cleanup(self):
        """정리 작업을 지연 실행"""
        if self._cleanup_timer is None:
            self._cleanup_timer = QTimer()
            self._cleanup_timer.setSingleShot(True)
            self._cleanup_timer.timeout.connect(self.perform_cleanup)
            self._cleanup_timer.start(100)  # 100ms 후 정리 실행
    
    def perform_cleanup(self):
        """실제 정리 작업 수행 - 메모리 손상 방지 강화"""
        if self._is_destroyed:  # 이미 파괴된 상태면 중복 정리 방지
            return
        
        # 정리 시작 전에 파일명 저장 (정리 후에는 접근 불가)
        filename = "unknown"
        try:
            if hasattr(self, 'image_data') and self.image_data:
                filename = self.image_data.get('filename', 'unknown')
        except:
            pass
        
        try:
            self._is_destroyed = True  # 정리 시작 시점부터 파괴 상태로 마킹
            
            # 시그널 연결 해제 - 더 안전한 방식
            try:
                self.disconnect_all_signals()
            except Exception as signal_error:
                logger.warning(f"시그널 해제 중 오류: {signal_error}")
            
            # 이미지 레이블 정리 - 더 안전한 방식
            try:
                if (hasattr(self, 'image_label') and self.image_label and 
                    hasattr(self.image_label, 'parent') and 
                    self.image_label.parent() is not None):
                    
                    # 이미지 데이터 정리
                    try:
                        self.image_label.clear()
                        self.image_label.setPixmap(QPixmap())
                    except RuntimeError:
                        pass  # Qt 객체가 이미 삭제된 경우
                    except Exception as clear_error:
                        logger.warning(f"이미지 레이블 정리 중 오류: {clear_error}")
                        
            except Exception as label_error:
                logger.warning(f"이미지 레이블 접근 중 오류: {label_error}")
            
            # 타이머 정리
            try:
                if hasattr(self, '_cleanup_timer') and self._cleanup_timer:
                    self._cleanup_timer.stop()
                    self._cleanup_timer.deleteLater()
                    self._cleanup_timer = None
            except Exception as timer_error:
                logger.warning(f"타이머 정리 중 오류: {timer_error}")
            
            # 이미지 데이터 참조 정리
            try:
                if hasattr(self, 'image_data'):
                    self.image_data = None
            except Exception as data_error:
                logger.warning(f"이미지 데이터 정리 중 오류: {data_error}")
            
            # 이미지 캐시 참조 정리
            try:
                if hasattr(self, 'image_cache'):
                    self.image_cache = None
            except Exception as cache_error:
                logger.warning(f"이미지 캐시 정리 중 오류: {cache_error}")
            
            logger.debug(f"GridImageWidget 정리 완료: {filename}")
            
        except Exception as e:
            logger.warning(f"GridImageWidget 정리 중 오류: {str(e)}")
        
        # 가비지 컬렉션 강제 실행
        import gc
        gc.collect()
    
    def disconnect_all_signals(self):
        """모든 시그널 연결 해제 - 메모리 손상 방지 강화"""
        try:
            # 저장된 시그널-슬롯 튜플들을 사용하여 정확히 해제
            if hasattr(self, '_connected_signals_and_slots'):
                for signal, slot in list(self._connected_signals_and_slots):  # 순회 중 리스트 변경 방지
                    try:
                        if signal and slot and hasattr(signal, 'disconnect'):
                            # 연결된 시그널이 있는지 확인 후 해제
                            if hasattr(signal, 'receivers') and signal.receivers(slot) > 0:
                                signal.disconnect(slot)
                                logger.debug(f"시그널-슬롯 해제 성공")
                            else:
                                logger.debug(f"시그널-슬롯 이미 해제됨")
                    except (RuntimeError, TypeError):
                        # 이미 연결 해제되었거나 유효하지 않은 경우 무시
                        logger.debug(f"시그널-슬롯 이미 해제됨")
                    except Exception as e:
                        logger.warning(f"특정 시그널-슬롯 해제 중 오류: {e}")
                
                # 연결 목록 초기화
                self._connected_signals_and_slots.clear()
                self._signals_connected = False
            
            logger.debug(f"GridImageWidget 시그널 모두 해제 완료")
            
        except Exception as e:
            logger.warning(f"시그널 해제 중 오류: {str(e)}")
    
    def connect_signals(self, clicked_slot, double_clicked_slot):
        """시그널 연결 - 추적 가능한 방식으로"""
        try:
            if not self._is_destroyed:
                # 기존 연결 해제
                self.disconnect_all_signals()
                
                # 새로운 연결 - 시그널과 슬롯을 튜플로 저장
                self.clicked.connect(clicked_slot)
                self._connected_signals_and_slots.append((self.clicked, clicked_slot))
                
                self.double_clicked.connect(double_clicked_slot)
                self._connected_signals_and_slots.append((self.double_clicked, double_clicked_slot))
                
                self._signals_connected = True
                logger.debug(f"GridImageWidget 시그널 연결 완료: {self.image_data.get('filename', 'unknown')}")
        except Exception as e:
            logger.error(f"시그널 연결 실패: {str(e)}")
    
    def disconnect_signals(self):
        """시그널 연결 해제 - 추적된 슬롯들을 사용"""
        self.disconnect_all_signals()
    
    def update_frame_style(self):
        """프레임 스타일 업데이트 - 메모리 안전 버전"""
        if self._is_destroyed:
            return
            
        try:
            # 위젯이 유효한지 확인
            if not hasattr(self, 'image_frame') or not self.image_frame:
                return
            
            if not hasattr(self.image_frame, 'setStyleSheet'):
                return
            
            # DynamoDB 제외 상태가 최우선 (빨간색 테두리 + 반투명 오버레이)
            if self.is_excluded_from_dynamodb:
                self.image_frame.setStyleSheet("""
                    QFrame {
                        border: 4px solid #dc3545;
                        border-radius: 4px;
                        background-color: rgba(220, 53, 69, 0.1);
                        position: relative;
                    }
                    QFrame:hover {
                        background-color: rgba(220, 53, 69, 0.2);
                    }
                """)
                
                # 제외 상태 오버레이 추가
                if not hasattr(self, 'exclude_overlay'):
                    self.exclude_overlay = QLabel("DynamoDB\n제외됨", self.image_frame)
                    self.exclude_overlay.setAlignment(Qt.AlignCenter)
                    self.exclude_overlay.setStyleSheet("""
                        QLabel {
                            background-color: rgba(220, 53, 69, 0.8);
                            color: white;
                            font-weight: bold;
                            font-size: 10px;
                            border-radius: 3px;
                            padding: 2px;
                        }
                    """)
                    self.exclude_overlay.setFixedSize(60, 30)
                    self.exclude_overlay.move(5, 5)  # 좌상단에 위치
                    
                self.exclude_overlay.setVisible(True)
                
            # 일반 선택 상태 (파란색 테두리)
            elif self.is_selected:
                self.image_frame.setStyleSheet("""
                    QFrame {
                        border: 3px solid #007bff;
                        border-radius: 4px;
                        background-color: #e3f2fd;
                    }
                """)
                # 제외 오버레이 숨기기
                if hasattr(self, 'exclude_overlay'):
                    self.exclude_overlay.setVisible(False)
                    
            # 기본 상태
            else:
                self.image_frame.setStyleSheet("""
                    QFrame {
                        border: 2px solid #dee2e6;
                        border-radius: 4px;
                        background-color: #f8f9fa;
                    }
                    QFrame:hover {
                        border-color: #007bff;
                        background-color: #f0f8ff;
                    }
                """)
                # 제외 오버레이 숨기기
                if hasattr(self, 'exclude_overlay'):
                    self.exclude_overlay.setVisible(False)
                    
        except RuntimeError:
            self._is_destroyed = True
        except Exception as e:
            logger.warning(f"프레임 스타일 업데이트 중 오류: {e}")
            self._is_destroyed = True
    
    def set_selected(self, selected: bool):
        """선택 상태 설정 - 메모리 안전 버전"""
        if self._is_destroyed:
            return
            
        try:
            self.is_selected = selected
            self.update_frame_style()
        except RuntimeError:
            self._is_destroyed = True
        except Exception as e:
            logger.warning(f"선택 상태 설정 중 오류: {e}")
            self._is_destroyed = True
    
    def set_excluded_from_dynamodb(self, excluded: bool):
        """DynamoDB 제외 상태 설정"""
        if self._is_destroyed:
            return
            
        try:
            self.is_excluded_from_dynamodb = excluded
            self.update_frame_style()
        except RuntimeError:
            self._is_destroyed = True
        except Exception as e:
            logger.warning(f"제외 상태 설정 중 오류: {e}")
            self._is_destroyed = True
    
    def load_image(self):
        """이미지 로드 - 메모리 안전 버전"""
        if self._is_destroyed:
            return
            
        try:
            # 이미지 데이터 유효성 확인
            if not hasattr(self, 'image_data') or not self.image_data:
                self.set_placeholder_image("데이터 없음")
                return
            
            url = self.image_data.get('url')
            if not url:
                self.set_placeholder_image("URL 없음")
                return
            
            # 로컬 segment 이미지인 경우 직접 로드
            if self.image_data.get('is_local_segment', False):
                self.load_local_segment_image()
                return
            
            if not self.image_cache:
                filename = self.image_data.get('filename', 'unknown')
                logger.error(f"이미지 캐시 없음: {filename}")
                self.set_placeholder_image("캐시 없음")
                return
            
            # 로딩 플레이스홀더 표시
            self.set_placeholder_image("로딩 중...")
            
            # 캐시에서 이미지 가져오기 - 안전한 콜백 사용
            try:
                cached_pixmap = self.image_cache.get_image(url, self.safe_on_image_loaded)
                
                if cached_pixmap:
                    self.set_image(cached_pixmap)
                else:
                    # 5초 후에도 로딩 중이면 문제가 있다고 가정
                    filename = self.image_data.get('filename', 'unknown')
                    timeout_checker = self._create_timeout_checker(filename)
                    QTimer.singleShot(5000, timeout_checker)
                    
            except Exception as cache_error:
                logger.warning(f"캐시에서 이미지 가져오기 실패: {cache_error}")
                self.set_placeholder_image("캐시 오류")
                
        except Exception as e:
            filename = getattr(self, 'image_data', {}).get('filename', 'unknown')
            logger.error(f"이미지 로드 오류 {filename}: {str(e)}")
            self.set_placeholder_image("로드 오류")
    
    def _create_timeout_checker(self, filename):
        """타임아웃 체커 생성 - 약한 참조 사용"""
        def timeout_checker():
            try:
                if not self._is_destroyed and hasattr(self, 'image_label') and self.image_label:
                    current_pixmap = self.image_label.pixmap()
                    if not current_pixmap or current_pixmap.isNull():
                        self.set_placeholder_image("타임아웃")
            except Exception as e:
                logger.error(f"타임아웃 체크 오류: {str(e)}")
        return timeout_checker
    
    def load_local_segment_image(self):
        """로컬 segment 이미지 직접 로드 - 메모리 안전 버전"""
        try:
            if self._is_destroyed:
                return
            
            # 이미지 데이터 유효성 확인
            if not hasattr(self, 'image_data') or not self.image_data:
                self.set_placeholder_image("데이터 없음")
                return
            
            local_path = self.image_data.get('local_path')
            if not local_path:
                self.set_placeholder_image("경로 없음")
                return
            
            # 파일 존재 확인
            if not os.path.exists(local_path):
                self.set_placeholder_image("파일 없음")
                return
            
            # QPixmap으로 직접 로드
            try:
                pixmap = QPixmap(local_path)
                
                if pixmap.isNull():
                    self.set_placeholder_image("로드 실패")
                else:
                    self.set_image(pixmap)
                    
            except Exception as pixmap_error:
                logger.warning(f"QPixmap 로드 실패: {pixmap_error}")
                self.set_placeholder_image("로드 실패")
                
        except Exception as e:
            logger.error(f"로컬 segment 이미지 로드 오류: {str(e)}")
            self.set_placeholder_image("오류 발생")
    
    def safe_on_image_loaded(self, url: str, pixmap: Optional[QPixmap]):
        """안전한 이미지 로드 완료 콜백 - 위젯 상태 확인"""
        # 위젯이 파괴되었거나 Qt 객체가 삭제된 경우 무시
        if self._is_destroyed:
            return
            
        try:
            # 부모 위젯이 여전히 유효한지 먼저 확인 (빠른 체크)
            if not self.parent():
                return
                
            # Qt 객체가 여전히 유효한지 확인
            if not self.image_label or not hasattr(self, 'image_label'):
                return
            
            # URL이 일치하지 않으면 무시
            widget_url = self.image_data.get('url', '')
            if url != widget_url:
                return
                
            # 이미지 설정
            if pixmap:
                self.set_image(pixmap)
            else:
                logger.error(f"이미지 로드 실패 - pixmap이 None: {self.image_data.get('filename', 'unknown')}")
                self.set_placeholder_image("로드 실패")
                
        except RuntimeError as e:
            # Qt 객체가 이미 삭제된 경우
            logger.warning(f"이미지 위젯이 이미 삭제됨: {str(e)}")
            self._is_destroyed = True
        except Exception as e:
            logger.error(f"이미지 로드 콜백 오류: {str(e)}")
            self.set_placeholder_image("콜백 오류")
    
    def on_image_loaded(self, url: str, pixmap: Optional[QPixmap]):
        """이미지 로드 완료 콜백 (deprecated - safe_on_image_loaded 사용)"""
        self.safe_on_image_loaded(url, pixmap)
    
    def set_placeholder_image(self, text: str):
        """플레이스홀더 이미지 생성 및 설정 - 메모리 안전 버전"""
        if self._is_destroyed:
            return
            
        try:
            # 이미지 레이블 유효성 확인
            if not hasattr(self, 'image_label') or not self.image_label:
                return
            
            if not hasattr(self.image_label, 'setPixmap'):
                return
            
            # 기존 image_viewer.py의 플레이스홀더 로직 참조
            try:
                placeholder = QPixmap(190, 190)
                placeholder.fill(QColor(245, 245, 245))  # 연한 회색 배경
                
                # 텍스트 그리기
                painter = QPainter(placeholder)
                painter.setPen(QPen(QColor(150, 150, 150)))
                
                # 폰트 설정
                font = QFont()
                font.setPointSize(9)
                font.setBold(True)
                painter.setFont(font)
                
                # 텍스트를 중앙에 그리기
                painter.drawText(placeholder.rect(), Qt.AlignCenter | Qt.TextWordWrap, text)
                painter.end()
                
                # 이미지 레이블에 설정
                try:
                    self.image_label.setPixmap(placeholder)
                except RuntimeError:
                    self._is_destroyed = True
                    
            except Exception as pixmap_error:
                logger.warning(f"플레이스홀더 생성 실패: {pixmap_error}")
                
        except RuntimeError as e:
            logger.warning(f"플레이스홀더 설정 중 Qt 객체 삭제됨: {str(e)}")
            self._is_destroyed = True
        except Exception as e:
            logger.error(f"플레이스홀더 설정 오류: {str(e)}")
    
    def set_image(self, pixmap: QPixmap):
        """이미지 설정 - 메모리 안전 버전"""
        if self._is_destroyed:
            return
            
        try:
            # 이미지 레이블 유효성 확인
            if not hasattr(self, 'image_label') or not self.image_label:
                return
            
            if not hasattr(self.image_label, 'setPixmap'):
                return
            
            if pixmap.isNull():
                self.set_placeholder_image("잘못된 이미지")
                return
            
            # 목표 크기 (여백 고려)
            target_size = QSize(190, 190)  # 200x200 라벨에서 여백 10px 고려
            original_size = pixmap.size()
            
            # 원본 이미지가 너무 작은 경우 원본 크기 유지
            if original_size.width() <= target_size.width() and original_size.height() <= target_size.height():
                try:
                    self.image_label.setPixmap(pixmap)
                except RuntimeError:
                    self._is_destroyed = True
                return
            
            # 비율을 유지하면서 목표 크기에 맞게 스케일링
            try:
                scale_x = target_size.width() / original_size.width()
                scale_y = target_size.height() / original_size.height()
                scale_factor = min(scale_x, scale_y)
                
                # 최종 크기 계산
                new_width = int(original_size.width() * scale_factor)
                new_height = int(original_size.height() * scale_factor)
                
                # 고품질 스케일링 적용
                scaled_pixmap = pixmap.scaled(
                    new_width, new_height,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                
                self.image_label.setPixmap(scaled_pixmap)
                
            except Exception as scaling_error:
                logger.warning(f"이미지 스케일링 실패: {scaling_error}")
                # 스케일링 실패 시 원본 이미지 사용
                try:
                    self.image_label.setPixmap(pixmap)
                except RuntimeError:
                    self._is_destroyed = True
                
        except RuntimeError as e:
            logger.warning(f"이미지 설정 중 Qt 객체 삭제됨: {str(e)}")
            self._is_destroyed = True
        except Exception as e:
            logger.error(f"이미지 설정 오류: {str(e)}")
    
    def on_clicked(self, event):
        """클릭 이벤트 - 메모리 안전 버전"""
        if self._is_destroyed:
            return
            
        if event.button() == Qt.LeftButton:
            try:
                # 이미지 데이터 유효성 확인
                if hasattr(self, 'image_data') and self.image_data:
                    self.clicked.emit(self.image_data)
            except RuntimeError:
                # 위젯이 파괴된 상태에서 시그널 발생 시도 시 무시
                self._is_destroyed = True
            except Exception as e:
                logger.error(f"클릭 이벤트 처리 오류: {str(e)}")
    
    def on_double_clicked(self, event):
        """더블클릭 이벤트 - 이미지 뷰어 열기 (메모리 안전 버전)"""
        if self._is_destroyed:
            return
            
        if event.button() == Qt.LeftButton:
            try:
                # 이미지 데이터 유효성 확인
                if hasattr(self, 'image_data') and self.image_data:
                    self.double_clicked.emit(self.image_data)
            except RuntimeError:
                # 위젯이 파괴된 상태에서 시그널 발생 시도 시 무시
                self._is_destroyed = True
            except Exception as e:
                logger.error(f"더블클릭 이벤트 처리 오류: {str(e)}")


class MainImageViewer(QWidget):
    """
    메인 이미지 뷰어 위젯 
        - representative_selected : Signal(dict, str) 대표 이미지 선택 후 우측 하단의 버튼을 누르면 해당 선택된 이미지 데이터, 타입 전달 
                                    => representative_panel의 메서드(add_representative_image) 에게 전달 
        - image_cache : 이미지 캐시(객체) 
        - current_images : 현재 이미지 리스트(딕셔너리) 
        - current_image_index : 현재 이미지 인덱스(정수) 
        - current_product : 현재 상품 데이터(딕셔너리) 
    """

    representative_selected = Signal(dict, str)  # 이미지 데이터, 타입
    
    def __init__(self):
        super().__init__()
        self.image_cache = None
        self.representative_panel = None
        self.current_images = []
        self.current_product = None
        self.aws_manager = None  # AWS Manager 추가
        
        # 선택 모드 상태 관리 추가
        self.selection_mode = None  # None, 'model_wearing', 'front_cutout', 'back_cutout', 'color_variant'
        self.mode_buttons = {}  # 모드 버튼들 저장
        
        # 이미지 이동 히스토리 관리
        self.move_history = []  # 이동 히스토리 [(image_data, from_folder, to_folder, timestamp), ...]
        self.pending_moves = []  # S3에 반영되지 않은 이동 목록 [(source_key, dest_key), ...]
        
        # DynamoDB 제외 기능 관리
        self.excluded_from_dynamodb = set()  # DynamoDB에서 제외할 이미지들의 식별자 저장
        self.exclude_mode_active = False     # 제외 모드 활성화 상태
        
        self.folder_tabs = {}
        self.curation_worker = None  # 큐레이션 워커
        self.setup_ui()
    
    def setup_ui(self):
        """UI 설정"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        
        # 키보드 포커스 설정 강화
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_KeyCompression, False)  # 키 압축 비활성화
        
        # 이벤트 필터 설치 (키보드 이벤트를 확실히 받기 위해)
        self.installEventFilter(self)
        
        # 탭 순서 설정으로 포커스 받을 수 있도록
        self.setTabOrder(self, self)
        
        # 헤더 영역 (5%)
        header_container = QWidget()
        header_layout = QVBoxLayout(header_container)
        header_layout.setContentsMargins(0, 0, 0, 0)
        self.setup_header(header_layout)
        layout.addWidget(header_container, stretch=5)
        
        # 이미지 영역 (80%)
        image_container = QWidget()
        image_layout = QVBoxLayout(image_container)
        image_layout.setContentsMargins(0, 0, 0, 0)
        self.setup_image_area(image_layout)
        layout.addWidget(image_container, stretch=80)
        
        # 모드 선택 영역 (10%)
        mode_container = QWidget()
        mode_layout = QVBoxLayout(mode_container)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        self.setup_mode_selection(mode_layout)
        layout.addWidget(mode_container, stretch=10)
        
        # 컨트롤 영역 (5%)
        control_container = QWidget()
        control_layout = QVBoxLayout(control_container)
        control_layout.setContentsMargins(0, 0, 0, 0)
        self.setup_controls(control_layout)
        layout.addWidget(control_container, stretch=5)
        
        # 키보드 단축키 설정
        self.setup_shortcuts()
    
    def setup_shortcuts(self):
        """키보드 단축키 설정 - 포커스 정책 강화"""
        # 위젯 포커스 정책 설정
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        
        # 숫자키 사용으로 직관적이고 충돌이 적은 키보드 입력 처리
        # 1-6 키는 직관적이고 다른 애플리케이션과 충돌이 적음
        
        # 위젯이 키보드 이벤트를 받을 수 있도록 설정
        self.setAttribute(Qt.WA_AcceptTouchEvents, False)  # 터치 이벤트 비활성화로 키보드 포커스 강화
        self.setContextMenuPolicy(Qt.NoContextMenu)  # 컨텍스트 메뉴 비활성화
    
    def activate_mode_button(self, mode_key: str):
        """모드 버튼 활성화"""
        try:
            logger.debug(f"activate_mode_button 호출됨: {mode_key}")
            
            # 먼저 포커스 확실히 설정
            self.ensure_keyboard_focus()
            
            if mode_key == 'clear_mode':
                self.clear_selection_mode()
            elif mode_key == 'image_viewer':
                self.open_image_viewer_button_clicked()
            elif mode_key == 'undo_move':
                self.undo_last_move()
            elif mode_key in self.mode_buttons:
                # 직접 모드 설정 (버튼 클릭 대신)
                self.set_selection_mode(mode_key)
                logger.debug(f"모드 {mode_key} 직접 설정 완료")
            else:
                logger.warning(f"알 수 없는 모드 키: {mode_key}")
                
        except Exception as e:
            logger.error(f"모드 버튼 활성화 오류: {str(e)}")
            # 오류 발생 시 버튼 복구 시도
            QTimer.singleShot(100, self.force_button_repair)
    
    def update_button_states(self, folder_name: str, image_data: dict):
        """선택된 이미지에 따라 버튼 상태 업데이트"""
        try:
            # Text 폴더로 이동 버튼: segment 폴더의 모든 이미지 가능 (S3 및 로컬 segment 포함)
            can_move_to_text = (folder_name == 'segment')
            self.move_to_text_btn.setEnabled(can_move_to_text)
            
            # 되돌리기 버튼: 이동 히스토리가 있을 때만 가능
            can_undo = len(self.move_history) > 0
            self.undo_btn.setEnabled(can_undo)
            
            # 버튼 툴팁 업데이트
            if can_move_to_text:
                filename = image_data.get('filename', 'Unknown')
                is_local = image_data.get('is_local_segment', False)
                if is_local:
                    self.move_to_text_btn.setToolTip(f"'{filename}'을 Text 폴더로 이동 (로컬 이미지)")
                else:
                    self.move_to_text_btn.setToolTip(f"'{filename}'을 Text 폴더로 이동")
            else:
                self.move_to_text_btn.setToolTip("Segment 폴더의 이미지만 Text 폴더로 이동 가능")
                
        except Exception as e:
            logger.error(f"버튼 상태 업데이트 오류: {str(e)}")
    
    def move_image_to_text(self):
        """선택된 이미지를 text 폴더로 이동"""
        try:
            # 현재 선택된 이미지 확인
            current_folder = list(self.folder_tabs.keys())[self.tab_widget.currentIndex()]
            if current_folder != 'segment':
                self.show_status_message("❌ Segment 폴더의 이미지만 이동 가능합니다", error=True)
                return
            
            selected_image = self.get_current_selected_image()
            if not selected_image:
                self.show_status_message("❌ 이동할 이미지를 선택해주세요", error=True)
                return
            
            # 이미지 이동 수행
            from datetime import datetime
            timestamp = datetime.now().isoformat()
            
            # 히스토리에 기록 (깊은 복사 사용)
            import copy
            move_record = {
                'image_data': copy.deepcopy(selected_image),
                'from_folder': 'segment',
                'to_folder': 'text',
                'timestamp': timestamp,
                'is_local_segment': selected_image.get('is_local_segment', False)
            }
            self.move_history.append(move_record)
            
            # S3 키 계산 (S3 이미지인 경우에만)
            is_local_segment = selected_image.get('is_local_segment', False)
            if not is_local_segment and self.current_product:
                
                # 제품 정보로 재구성
               
                filename = selected_image.get('filename', '')
                if filename:
                    main_category = self.current_product.get('main_category', '')
                    sub_category = self.current_product.get('sub_category', '')
                    product_id = self.current_product.get('product_id', '')
                    
                    if all([main_category, sub_category, product_id]):
                        source_key = f"{main_category}/{sub_category}/{product_id}/segment/{filename}"
                            
                
                if source_key:
                    # 소스 키에서 폴더 부분을 text로 변경하여 대상 키 생성
                    # 예: "TOP/1005/492294/segment/1_16.jpg" -> "TOP/1005/492294/text/1_16.jpg"
                    key_parts = source_key.split('/')
                    key_parts[-2] = 'text'  # 폴더 부분을 text로 변경
                    dest_key = '/'.join(key_parts)
                    
                    self.pending_moves.append((source_key, dest_key))
                    logger.debug(f"S3 이동 작업 추가: {source_key} -> {dest_key}")
                else:
                    logger.warning(f"이미지에서 S3 키를 찾을 수 없습니다: {selected_image.get('filename', 'unknown')}")
            
            # 로컬 상태에서 이미지 이동
            self.move_image_local(selected_image, 'segment', 'text')
            
            filename = selected_image.get('filename', 'Unknown')
            if is_local_segment:
                self.show_status_message(f"✅ '{filename}'을 Text 폴더로 이동했습니다 (로컬 이미지)")
            else:
                self.show_status_message(f"✅ '{filename}'을 Text 폴더로 이동했습니다")
            
            # 버튼 상태 업데이트
            self.update_all_button_states()
            
            # 포커스 복원 (위젯 재생성 완료 후)
            QTimer.singleShot(200, self.restore_focus_after_move)
            
        except Exception as e:
            logger.error(f"이미지 이동 오류: {str(e)}")
            self.show_status_message(f"❌ 이미지 이동 실패: {str(e)}", error=True)
    
    def restore_focus_after_move(self):
        """이미지 이동 후 포커스 복원"""
        try:
            # 메인 이미지 뷰어에 강력하게 포커스 설정
            self.setFocus(Qt.OtherFocusReason)
            self.activateWindow()
            self.raise_()  # 위젯을 맨 앞으로
            
            # 키보드 포커스 정책 재설정
            self.setFocusPolicy(Qt.StrongFocus)
            
            # 부모 윈도우도 활성화
            if self.window():
                self.window().activateWindow()
                self.window().raise_()
            
            logger.debug("이미지 이동 후 포커스 복원 완료")
            
        except Exception as e:
            logger.error(f"포커스 복원 오류: {str(e)}")
    
    def move_image_local(self, image_data: dict, from_folder: str, to_folder: str):
        """로컬 상태에서 이미지를 폴더 간 이동 - 메모리 안전 버전"""
        try:
            # from_folder에서 이미지 제거
            from_tab_data = self.folder_tabs.get(from_folder)
            if from_tab_data and image_data in from_tab_data['images']:
                from_tab_data['images'].remove(image_data)
            
            # 깊은 복사로 새로운 이미지 데이터 생성 (메모리 안전)
            import copy
            moved_image_data = copy.deepcopy(image_data)
            moved_image_data['folder'] = to_folder
            
            # 로컬 segment 이미지인 경우 키(key) 업데이트
            if moved_image_data.get('is_local_segment', False):
                # 새로운 캐시 구조에 맞는 키 업데이트
                if self.current_product:
                    main_category = self.current_product.get('main_category', '')
                    sub_category = self.current_product.get('sub_category', '')
                    product_id = self.current_product.get('product_id', '')
                    filename = moved_image_data.get('filename', '')
                    
                    if all([main_category, sub_category, product_id, filename]):
                        # S3 키를 새로운 폴더로 업데이트
                        moved_image_data['key'] = f"{main_category}/{sub_category}/{product_id}/{to_folder}/{filename}"
                    else:
                        # 폴백: 간단한 키 형식
                        moved_image_data['key'] = f"{to_folder}/{filename}"
                else:
                    # 제품 정보가 없는 경우 폴백
                    filename = moved_image_data.get('filename', '')
                    moved_image_data['key'] = f"{to_folder}/{filename}"
                
                # 기존 legacy 방식의 키 처리
                if moved_image_data.get('is_legacy', False):
                    filename = moved_image_data.get('filename', '')
                    if from_folder == 'segment' and to_folder == 'text':
                        # segments/filename -> text/filename
                        moved_image_data['key'] = f"text/{filename}"
                    elif from_folder == 'text' and to_folder == 'segment':
                        # text/filename -> segments/filename (되돌리기 시)
                        moved_image_data['key'] = f"segments/{filename}"
            
            # S3 이미지인 경우에도 키 업데이트
            elif not moved_image_data.get('is_local_segment', False):
                original_key = moved_image_data.get('key', '')
                if '/' in original_key:
                    # S3 키 형식: {main_category}/{sub_category}/{product_id}/{folder}/{filename}
                    key_parts = original_key.split('/')
                    if len(key_parts) >= 2:
                        filename = key_parts[-1]
                        if len(key_parts) >= 4:
                            # 전체 경로 형식
                            key_parts[-2] = to_folder  # 폴더 부분만 변경
                            moved_image_data['key'] = '/'.join(key_parts)
                        else:
                            # 간단한 형식
                            moved_image_data['key'] = f"{to_folder}/{filename}"
            
            to_tab_data = self.folder_tabs.get(to_folder)
            if to_tab_data:
                to_tab_data['images'].append(moved_image_data)
            
            # 전체 이미지 목록에서도 업데이트 (참조 안전하게)
            for i, img in enumerate(self.current_images):
                if img == image_data:
                    self.current_images[i] = moved_image_data
                    break
            
            # 디스플레이 업데이트를 지연 실행 (메모리 안전)
            # 위젯 정리 전에 폴더명을 미리 저장
            from_folder_copy = from_folder
            to_folder_copy = to_folder
            
            QTimer.singleShot(50, lambda: self._safe_update_folder_display_with_focus(from_folder_copy))
            QTimer.singleShot(100, lambda: self._safe_update_folder_display_with_focus(to_folder_copy))
            
            logger.debug(f"로컬 이미지 이동 완료: {from_folder} -> {to_folder}")
            logger.debug(f"업데이트된 키: {moved_image_data.get('key', 'N/A')}")
            
        except Exception as e:
            logger.error(f"로컬 이미지 이동 오류: {str(e)}")
            raise
    
    def _safe_update_folder_display_with_focus(self, folder_name: str):
        """안전한 폴더 디스플레이 업데이트 - 포커스 복원 포함"""
        try:
            # 기존 디스플레이 업데이트 수행
            self._safe_update_folder_display(folder_name)
            
            # 업데이트 완료 후 포커스 복원 (약간의 지연 후)
            QTimer.singleShot(50, self.ensure_keyboard_focus)
            
        except Exception as e:
            logger.error(f"포커스가 포함된 폴더 디스플레이 업데이트 오류 {folder_name}: {str(e)}")
    
    def ensure_keyboard_focus(self):
        """키보드 포커스 확실히 설정"""
        try:
            # MainImageViewer에 강력하게 포커스 설정
            if not self.hasFocus():
                self.setFocus(Qt.OtherFocusReason)
                
            # 키보드 포커스 정책 재확인
            if self.focusPolicy() != Qt.StrongFocus:
                self.setFocusPolicy(Qt.StrongFocus)
            
            # 윈도우 활성화
            self.activateWindow()
            
            # 부모 윈도우가 있으면 그것도 활성화
            parent_window = self.window()
            if parent_window and parent_window != self:
                parent_window.activateWindow()
                parent_window.raise_()
            
            logger.debug("키보드 포커스 확실히 설정 완료")
            
        except Exception as e:
            logger.error(f"키보드 포커스 설정 오류: {str(e)}")
    
    def _safe_update_folder_display(self, folder_name: str):
        """안전한 폴더 디스플레이 업데이트 - 메모리 손상 방지"""
        try:
            if not hasattr(self, 'folder_tabs') or folder_name not in self.folder_tabs:
                return
            
            tab_data = self.folder_tabs[folder_name]
            if not tab_data or 'grid_layout' not in tab_data:
                return
            
            # 기존 위젯들을 안전하게 정리
            self.safe_cleanup_widgets(tab_data)
            
            # 레이아웃 정리
            self.clear_grid_layout(tab_data['grid_layout'])
            
            # 새로운 위젯들 생성
            grid_layout = tab_data['grid_layout']
            image_widgets = []
            
            for image_data in tab_data['images']:
                try:
                    image_widget = GridImageWidget(image_data, self.image_cache)
                    
                    # 시그널 연결 - partial 함수 객체를 명시적으로 저장
                    try:
                        from functools import partial
                        clicked_slot = partial(self.on_image_selected, folder_name)
                        double_clicked_slot = self.open_image_viewer
                        
                        # 시그널 연결 (GridImageWidget 내부에서 튜플로 저장됨)
                        image_widget.connect_signals(clicked_slot, double_clicked_slot)
                        
                    except Exception as e:
                        logger.error(f"시그널 연결 실패: {str(e)}")
                        continue
                    
                    grid_layout.addWidget(image_widget)
                    image_widgets.append(image_widget)
                    
                except Exception as e:
                    logger.error(f"이미지 위젯 생성 실패: {str(e)}")
                    continue
            
            # 위젯 목록 업데이트
            tab_data['image_widgets'] = image_widgets
            
            # 선택된 이미지 상태 복원
            if tab_data.get('selected_image_data'):
                self._restore_selection_state(tab_data)
            
            logger.debug(f"안전한 폴더 디스플레이 업데이트 완료: {folder_name}")
            
        except Exception as e:
            logger.error(f"안전한 폴더 디스플레이 업데이트 오류 {folder_name}: {str(e)}")
    
    def _restore_selection_state(self, tab_data):
        """선택 상태 복원"""
        try:
            selected_image_data = tab_data.get('selected_image_data')
            if not selected_image_data:
                return
            
            # 파일명으로 해당 위젯 찾기
            for widget in tab_data.get('image_widgets', []):
                if (hasattr(widget, 'image_data') and 
                    widget.image_data.get('filename') == selected_image_data.get('filename')):
                    widget.set_selected(True)
                    break
        except Exception as e:
            logger.error(f"선택 상태 복원 오류: {str(e)}")
    
    def undo_last_move(self):
        """마지막 이동 작업을 되돌리기"""
        try:
            if not self.move_history:
                self.show_status_message("❌ 되돌릴 이동 기록이 없습니다", error=True)
                return
            
            # 마지막 이동 기록 가져오기
            last_move = self.move_history.pop()
            
            # 대기 중인 S3 이동에서도 제거 (S3 이미지인 경우에만)
            was_local_segment = last_move.get('is_local_segment', False)
            if not was_local_segment and self.pending_moves:
                # 마지막 이동과 매칭되는 S3 이동 제거
                self.pending_moves.pop()
            
            # 원본 이미지 데이터와 이동 정보
            original_image_data = last_move['image_data']
            from_folder = last_move['to_folder']  # 되돌리기이므로 to/from 반대
            to_folder = last_move['from_folder']
            filename = original_image_data.get('filename', 'Unknown')
            
            # 현재 from_folder에서 해당 이미지를 찾기 (파일명으로 매칭)
            from_tab_data = self.folder_tabs.get(from_folder)
            if not from_tab_data:
                raise Exception(f"{from_folder} 폴더를 찾을 수 없습니다")
            
            # 파일명으로 현재 이미지 데이터를 찾기
            current_image_data = None
            for img in from_tab_data['images']:
                if img.get('filename') == filename:
                    current_image_data = img
                    break
            
            if not current_image_data:
                raise Exception(f"{from_folder} 폴더에서 '{filename}' 이미지를 찾을 수 없습니다")
            
            # 현재 이미지 데이터를 사용해서 이동
            self.move_image_local(current_image_data, from_folder, to_folder)
            
            # 성공 메시지 표시
            if was_local_segment:
                self.show_status_message(f"↶ '{filename}'을 {to_folder.upper()} 폴더로 되돌렸습니다 (로컬 이미지)")
            else:
                self.show_status_message(f"↶ '{filename}'을 {to_folder.upper()} 폴더로 되돌렸습니다")
            
            # 버튼 상태 업데이트
            self.update_all_button_states()
            
            # 포커스 복원 (위젯 재생성 완료 후)
            QTimer.singleShot(200, self.restore_focus_after_move)
            
            logger.debug(f"되돌리기 완료: {from_folder} -> {to_folder}, 파일: {filename}")
            
        except Exception as e:
            logger.error(f"되돌리기 오류: {str(e)}")
            self.show_status_message(f"❌ 되돌리기 실패: {str(e)}", error=True)
    
    def update_all_button_states(self):
        """모든 버튼 상태 업데이트"""
        try:
            self.undo_btn.setEnabled(len(self.move_history) > 0)
            
            # 현재 선택된 이미지가 있으면 해당 버튼도 업데이트
            current_folder = list(self.folder_tabs.keys())[self.tab_widget.currentIndex()]
            tab_data = self.folder_tabs.get(current_folder)
            if tab_data and tab_data.get('selected_image_data'):
                self.update_button_states(current_folder, tab_data['selected_image_data'])
            else:
                self.move_to_text_btn.setEnabled(False)
        except Exception as e:
            logger.error(f"전체 버튼 상태 업데이트 오류: {str(e)}")
    
    def show_status_message(self, message: str, error: bool = False):
        """상태 메시지 표시"""
        try:
            if error:
                self.current_mode_label.setStyleSheet("color: #dc3545; font-size: 11px; background-color: transparent; font-weight: bold;")
            else:
                self.current_mode_label.setStyleSheet("color: #28a745; font-size: 11px; background-color: transparent; font-weight: bold;")
            
            self.current_mode_label.setText(message)
            
            # 3초 후 원래 메시지로 복원
            QTimer.singleShot(3000, self.restore_default_mode_message)
            
        except Exception as e:
            logger.error(f"상태 메시지 표시 오류: {str(e)}")
    
    def restore_default_mode_message(self):
        """기본 모드 메시지 복원"""
        try:
            self.current_mode_label.setText("모드를 선택하고 이미지를 클릭하세요 (1:모델, 2:정면, 3:후면, 4:색상, 5:DB제외, ESC:취소, V:뷰어, Ctrl+Z:되돌리기, Ctrl+R:복구, M:이동, Tab:탭이동)")
            self.current_mode_label.setStyleSheet("color: #6c757d; font-size: 11px; background-color: transparent;")
        except Exception as e:
            logger.error(f"기본 메시지 복원 오류: {str(e)}")
    
    def update_color_info_display(self, product_id: str):
        """meta.json에서 색상 정보를 읽어와서 표시"""
        try:
            if not self.image_cache or not product_id:
                self.color_info_label.setVisible(False)
                return
            
            # 로컬 캐시에서 meta.json 읽기
            meta_data = self.image_cache.get_product_meta_json(product_id)
            
            if not meta_data:
                self.color_info_label.setVisible(False)
                return
            
            # color_info 키 값 추출
            color_info = meta_data.get('color_info')
            is_size_info = meta_data.get('is_size_detail_info')
            num_likes = meta_data.get('num_likes')
            review_count = meta_data.get('review_count')
            avg_rating = meta_data.get('avg_rating')
            
            meta_info = f"사이즈 정보: {is_size_info} , 좋아요 수: {num_likes}, 리뷰 수: {review_count}, 평균 평점: {avg_rating}"
            
            if not color_info:
                self.color_info_label.setVisible(False)
                return
            
            # 색상 정보가 문자열인지 리스트인지 확인
            if isinstance(color_info, str):
                if color_info == "one_color":
                    display_text = "색상 정보: 단일 색상 (참고용)" +"\n" + meta_info
                    bg_color = "#e8f5e8"
                    border_color = "#4caf50"
                    text_color = "#2e7d32"
                else:
                    display_text = f"색상 정보: {color_info} (참고용)" +"\n" + meta_info
                    bg_color = "#f0f8ff"
                    border_color = "#4682b4"
                    text_color = "#2c3e50"
            elif isinstance(color_info, list):
                color_count = len(color_info)
                colors_text = ", ".join(str(c) for c in color_info)
                display_text = f"색상 정보: {color_count}개 색상 ({colors_text}) - 참고용" +"\n" + meta_info
                
                # 색상 개수에 따라 배경색 변경
                if color_count == 1:
                    bg_color = "#e8f5e8"
                    border_color = "#4caf50"
                    text_color = "#2e7d32"
                elif color_count == 2:
                    bg_color = "#fff3e0"
                    border_color = "#ff9800"
                    text_color = "#e65100"
                else:
                    bg_color = "#ffebee"
                    border_color = "#f44336"
                    text_color = "#c62828"
            else:
                display_text = f"색상 정보: {str(color_info)} (참고용)" +"\n" + meta_info
                bg_color = "#f0f8ff"
                border_color = "#4682b4"
                text_color = "#2c3e50"
            
            # 스타일 적용
            self.color_info_label.setText(display_text)
            self.color_info_label.setStyleSheet(f"""
                QLabel {{
                    background-color: {bg_color};
                    border: 1px solid {border_color};
                    color: {text_color};
                    padding: 4px 6px;
                    border-radius: 3px;
                    font-weight: bold;
                    font-size: 10px;
                    margin: 0px 5px;
                }}
            """)
            self.color_info_label.setVisible(True)
            
        except Exception as e:
            logger.error(f"색상 정보 표시 오류: {e}")
            self.color_info_label.setVisible(False)
    
    def setup_header(self, parent_layout):
        """헤더 설정"""
        header_frame = QFrame()
        header_frame.setStyleSheet("background-color: #f8f9fa; color: #212529; border-bottom: 1px solid #dee2e6; border-radius: 3px;")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(5, 10, 5, 10)
        
        # 제목
        title_label = QLabel("이미지 뷰어")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(10)
        title_label.setFont(title_font)
        header_layout.addWidget(title_label)
        
        # header_layout.addStretch()
        
        # 중앙 영역: 이미지 정보와 색상 정보 (수평 배치)
        info_layout = QHBoxLayout()
        
        # 이미지 정보 (최소 폭 설정)
        self.image_info_label = QLabel("이미지를 선택해주세요")
        self.image_info_label.setStyleSheet("color: #495057; background-color: transparent;")
        self.image_info_label.setAlignment(Qt.AlignCenter)
        self.image_info_label.setMinimumWidth(70)  # 최소 폭 설정
        info_layout.addWidget(self.image_info_label)  # stretch factor 1
        
        # 색상 정보 (기본적으로 숨김, 더 넓은 공간 할당)
        self.color_info_label = QLabel("")
        self.color_info_label.setVisible(False)
        self.color_info_label.setWordWrap(True)
        self.color_info_label.setAlignment(Qt.AlignCenter)
        self.color_info_label.setMinimumWidth(300)  # 최소 폭 설정
        self.color_info_label.setMaximumWidth(700)  # 최대 폭 제한
        info_layout.addWidget(self.color_info_label, 2)  # stretch factor 2 (더 넓은 공간)
        
        header_layout.addLayout(info_layout)
        
        # 도움말 버튼 (meta.json 보기)
        self.help_button = QPushButton("📋 상품 정보")
        self.help_button.setFixedHeight(25)
        self.help_button.setStyleSheet("""
            QPushButton {
                background-color: #17a2b8;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
                margin-left: 10px;
            }
            QPushButton:hover {
                background-color: #138496;
            }
            QPushButton:disabled {
                background-color: #6c757d;
                color: #adb5bd;
            }
        """)
        self.help_button.setEnabled(False)  # 기본적으로 비활성화
        self.help_button.clicked.connect(self.show_meta_json)
        header_layout.addWidget(self.help_button)
        
        parent_layout.addWidget(header_frame)
    
    def setup_image_area(self, parent_layout):
        """이미지 표시 영역 설정"""
        # 탭 위젯으로 폴더별 분류
        self.tab_widget = QTabWidget()
        
        # 각 폴더별 탭 생성
        self.folder_tabs = {}
        folders = ['detail', 'segment', 'summary', 'text']
        
        for folder in folders:
            tab_widget, tab_data = self.create_folder_tab(folder)
            self.folder_tabs[folder] = tab_data
            self.tab_widget.addTab(tab_widget, folder.capitalize())
        
        parent_layout.addWidget(self.tab_widget)
    
    def create_folder_tab(self, folder_name):
        """폴더별 탭 생성"""
        tab_widget = QWidget()
        layout = QVBoxLayout(tab_widget)
        
        # 상단 정보 영역
        info_layout = QHBoxLayout()
        
        # 폴더 이름과 이미지 수
        folder_info_label = QLabel(f"{folder_name.upper()} 폴더")
        folder_info_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #212529; background-color: transparent;")
        info_layout.addWidget(folder_info_label)
        
        info_layout.addStretch()
        
        # 이미지 카운터
        image_counter = QLabel("0개 이미지")
        image_counter.setStyleSheet("color: #495057; background-color: transparent; font-size: 12px;")
        info_layout.addWidget(image_counter)
        
        layout.addLayout(info_layout)
        
        # 이미지 그리드 스크롤 영역
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # 이미지 그리드 위젯
        grid_widget = QWidget()
        grid_layout = QGridLayout(grid_widget)
        grid_layout.setSpacing(5)
        grid_layout.setContentsMargins(5, 5, 5, 5)
        
        scroll_area.setWidget(grid_widget)
        layout.addWidget(scroll_area)
        
        # 하단 컨트롤 영역
        controls_layout = QHBoxLayout()
        
        # 선택된 이미지 정보
        selected_info_label = QLabel("이미지를 선택해주세요")
        selected_info_label.setStyleSheet("color: #495057; background-color: transparent; font-size: 11px;")
        controls_layout.addWidget(selected_info_label)
        
        controls_layout.addStretch()
        
        # 사용법 안내
        usage_info_label = QLabel("💡 이미지 더블클릭으로 뷰어 열기")
        usage_info_label.setStyleSheet("color: #6c757d; background-color: transparent; font-size: 10px; font-style: italic;")
        controls_layout.addWidget(usage_info_label)
        
        layout.addLayout(controls_layout)
        
        # 위젯들을 딕셔너리로 저장
        tab_data = {
            'widget': tab_widget,
            'grid_widget': grid_widget,
            'grid_layout': grid_layout,
            'image_counter': image_counter,
            'selected_info_label': selected_info_label,
            'images': [],
            'selected_image_data': None,
            'image_widgets': []  # 그리드에 생성된 이미지 위젯들
        }
        
        return tab_widget, tab_data
    
    def setup_mode_selection(self, parent_layout):
        """모드 선택 버튼 영역 설정"""
        mode_frame = QFrame()
        mode_frame.setStyleSheet("""
            QFrame {
                background-color: #f8f9fa; 
                border: 1px solid #dee2e6; 
                border-radius: 4px;
                padding: 5px;
            }
        """)
        mode_layout = QVBoxLayout(mode_frame)
        mode_layout.setContentsMargins(10, 5, 10, 5)
        mode_layout.setSpacing(4)
        
        # 안내 레이블
        info_label = QLabel("대표 이미지 선택 및 이미지 관리 (단축키: 1-5, V, ESC, Ctrl+Z, Ctrl+R):")
        info_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #495057; background-color: transparent;")
        mode_layout.addWidget(info_label)
        
        # 버튼 레이아웃
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)
        
        # 모드 버튼들 정의 (키보드 단축키 포함)
        mode_configs = [
            ('model_wearing', '(1) 모델', '#28a745', '#1e7e34'),
            ('front_cutout', '(2) 정면', '#007bff', '#0056b3'),
            ('back_cutout', '(3) 후면', '#6f42c1', '#5a2d91'),
            ('color_variant', '(4) 제품 색상', '#fd7e14', '#e55100'),
            ('exclude_from_dynamodb', '(5) DB제외', '#dc3545', '#c82333')
        ]
        
        for mode_key, mode_text, color, hover_color in mode_configs:
            btn = QPushButton(mode_text)
            btn.setFixedHeight(30)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color};
                    color: white;
                    border: none;
                    padding: 4px 8px;
                    border-radius: 3px;
                    font-weight: bold;
                    font-size: 10px;
                }}
                QPushButton:hover {{
                    background-color: {hover_color};
                }}
                QPushButton:pressed {{
                    background-color: {hover_color};
                    border: 1px solid rgba(255,255,255,0.3);
                }}
                QPushButton:checked {{
                    background-color: {hover_color};
                    border: 2px solid #fff;
                }}
            """)
            btn.setCheckable(True)
            
            # Lambda 함수 클로저 문제 해결: 함수 팩토리 패턴 사용
            def create_mode_click_handler(mode):
                return lambda checked: self.set_selection_mode(mode)
            
            # btn.clicked.connect(create_mode_click_handler(mode_key))
            
            if mode_key == 'exclude_from_dynamodb':
                # DynamoDB 제외 버튼은 별도 처리
                def create_exclude_click_handler():
                    return lambda checked: self.set_exclude_mode(checked)
                btn.clicked.connect(create_exclude_click_handler())
            else:
                # 기존 대표 이미지 선택 버튼들
                def create_mode_click_handler(mode):
                    return lambda checked: self.set_selection_mode(mode)
                btn.clicked.connect(create_mode_click_handler(mode_key))
            
            
            self.mode_buttons[mode_key] = btn
            buttons_layout.addWidget(btn)
        
        # 취소 버튼
        cancel_btn = QPushButton("(ESC) 선택 취소")
        cancel_btn.setFixedHeight(30)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                padding: 4px 8px;
                border-radius: 3px;
                font-weight: bold;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #5a6268;
            }
        """)
        cancel_btn.clicked.connect(self.clear_selection_mode)
        buttons_layout.addWidget(cancel_btn)
        
        buttons_layout.addStretch()
        mode_layout.addLayout(buttons_layout)
        
        # 현재 모드 표시 레이블
        self.current_mode_label = QLabel("모드를 선택하고 이미지를 클릭하세요 (1:모델, 2:정면, 3:후면, 4:색상, ESC:취소, V:뷰어, Ctrl+Z:되돌리기, Ctrl+R:복구, M:이동, Tab:탭이동)")
        self.current_mode_label.setStyleSheet("color: #6c757d; font-size: 11px; background-color: transparent;")
        mode_layout.addWidget(self.current_mode_label)
        
        parent_layout.addWidget(mode_frame)
    
    def set_selection_mode(self, mode):
        """선택 모드 설정"""
        logger.info(f"set_selection_mode 호출됨: {mode}")
        logger.info(f"현재 mode_buttons: {list(self.mode_buttons.keys())}")
        
        try:
            # 포커스 강제 설정
            self.ensure_keyboard_focus()
            
            # 이전 버튼 선택 해제
            for btn in self.mode_buttons.values():
                if btn and btn.isCheckable():
                    btn.setChecked(False)
            
            # 새로운 모드 설정
            self.selection_mode = mode
            if mode in self.mode_buttons and self.mode_buttons[mode]:
                button = self.mode_buttons[mode]
                if button.isCheckable():
                    button.setChecked(True)
                    logger.info(f"버튼 {mode} 체크됨")
                else:
                    logger.warning(f"버튼 {mode}가 체크 가능하지 않음")
            else:
                logger.error(f"버튼 {mode}를 찾을 수 없음")
            
            # 모드별 안내 메시지
            mode_messages = {
                'model_wearing': "모델 착용 이미지를 선택하세요",
                'front_cutout': "정면 누끼 이미지를 선택하세요", 
                'back_cutout': "후면 누끼 이미지를 선택하세요",
                'color_variant': "제품 색상 이미지를 선택하세요",
            }
            
            self.current_mode_label.setText(mode_messages.get(mode, "이미지를 선택하세요"))
            self.current_mode_label.setStyleSheet("color: #28a745; font-size: 11px; background-color: transparent; font-weight: bold;")
            
            logger.debug(f"선택 모드 설정 완료: {mode}")
            
        except Exception as e:
            logger.error(f"선택 모드 설정 오류: {str(e)}")
            # 오류 발생 시 버튼 복구 시도
            QTimer.singleShot(100, self.force_button_repair)
    
    def clear_selection_mode(self):
        """선택 모드 및 제외 모드 초기화"""
        try:
            logger.debug("선택 모드 초기화 시작")
            self.selection_mode = None
            
            # DynamoDB 제외 모드도 함께 취소
            if self.exclude_mode_active:
                self.set_exclude_mode(False)
            
            # 버튼 상태 안전하게 초기화
            for btn in self.mode_buttons.values():
                if btn and btn.isCheckable():
                    btn.setChecked(False)
            
            # 기본 메시지 복원 (5번 키 추가)
            self.current_mode_label.setText("모드를 선택하고 이미지를 클릭하세요 (1:모델, 2:정면, 3:후면, 4:색상, 5:DB제외, ESC:취소, V:뷰어, Ctrl+Z:되돌리기, Ctrl+R:복구, M:이동, Tab:탭이동)")
            self.current_mode_label.setStyleSheet("color: #6c757d; font-size: 11px; background-color: transparent;")
            
            logger.debug("선택 모드 초기화 완료")
            
        except Exception as e:
            logger.error(f"선택 모드 초기화 오류: {str(e)}")
    
    def setup_controls(self, parent_layout):
        """하단 컨트롤 설정 - 이미지 관리 버튼들"""
        controls_frame = QFrame()
        controls_frame.setStyleSheet("background-color: #f8f9fa; color: #212529; border-top: 1px solid #dee2e6; border-radius: 5px;")
        controls_layout = QVBoxLayout(controls_frame)

        # 하단 컨트롤 영역의 여백(마진) 설정: 왼쪽 15, 위 5, 오른쪽 15, 아래 5 픽셀
        controls_layout.setContentsMargins(15, 5, 15, 5)
        # 하단 컨트롤 영역의 위젯(버튼 등) 사이의 세로 간격을 10픽셀로 설정합니다.
        controls_layout.setSpacing(10)
        
        # 하단: 버튼들
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)
        
        # 텍스트 폴더로 이동 버튼
        self.move_to_text_btn = QPushButton("📝 Text 폴더로 이동 (M)")
        self.move_to_text_btn.setToolTip("선택된 Segment 이미지(S3 및 로컬 생성)를 Text 폴더로 이동 (단축키: M)")
        self.move_to_text_btn.setEnabled(False)
        self.move_to_text_btn.clicked.connect(self.move_image_to_text)
        self.move_to_text_btn.setStyleSheet("""
            QPushButton {
                background-color: #fd7e14;
                color: white;
                border: none;
                padding: 4px 4px;
                border-radius: 3px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #e55100;
            }
            QPushButton:disabled {
                background-color: #6c757d;
                color: #adb5bd;
            }
        """)
        buttons_layout.addWidget(self.move_to_text_btn)
        
        # 되돌리기 버튼
        self.undo_btn = QPushButton("↶ (Ctrl+Z) 되돌리기")
        self.undo_btn.setToolTip("마지막 이동 작업을 되돌립니다")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self.undo_last_move)
        self.undo_btn.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                padding: 4px 4px;
                border-radius: 3px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #5a6268;
            }
            QPushButton:disabled {
                background-color: #6c757d;
                color: #adb5bd;
            }
        """)
        buttons_layout.addWidget(self.undo_btn)
        
        buttons_layout.addStretch()
        
        # 이미지 뷰어 버튼
        viewer_btn = QPushButton("🖼️ (V) 이미지 뷰어")
        viewer_btn.setToolTip("선택된 이미지를 고급 뷰어로 열기 (더블클릭도 가능)")
        viewer_btn.clicked.connect(self.open_image_viewer_button_clicked)
        viewer_btn.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                padding: 4px 4px;
                border-radius: 3px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #218838;
            }
        """)
        buttons_layout.addWidget(viewer_btn)
        
        # 무신사 상품 페이지 버튼 (맨 우측)
        musinsa_btn = QPushButton("🛍️ 무신사 상품 페이지")
        musinsa_btn.setToolTip("현재 상품의 무신사 웹페이지 열기")
        musinsa_btn.clicked.connect(self.open_musinsa_page)
        musinsa_btn.setStyleSheet("""
            QPushButton {
                background-color: #6f42c1;
                color: white;
                border: none;
                padding: 4px 4px;
                border-radius: 3px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #5a2d91;
            }
        """)
        buttons_layout.addWidget(musinsa_btn)
        
        controls_layout.addLayout(buttons_layout)
        parent_layout.addWidget(controls_frame)
    
    def set_image_cache(self, image_cache):
        """이미지 캐시 설정"""
        self.image_cache = image_cache
    
    def set_aws_manager(self, aws_manager):
        """AWS Manager 설정"""
        self.aws_manager = aws_manager
    
    def set_representative_panel(self, representative_panel):
        """대표 이미지 패널 참조 설정
            - MainImageViewer 에서 RepresentativePanel을 참조하기 위한 설정
        """
        self.representative_panel = representative_panel

    #NOTE : 좌측 패널에서 클릭하여 얻은 s3, dyanomdb 정보 받아서 s3로 부터 이미지 캐쉬 디렉토리에 다운로드 한 뒤에 폴더 별로 정리 
    def load_product_images(self, images: List[Dict[str, Any]], product_data: Dict[str, Any]):
        """상품 이미지 로드
        args:
            images : s3 데이터 다운 받을 수 있는 url 정보 및 메타 정보 담기 리스트[딕셔너리] \n
                    이미지 정보 리스트 [{'key': '...', 'url': '...', 'folder': '...', 'filename': '...'}]
            product_data : dynamoDB에서 조회한 상품 개별 딕셔너리 정보
        return:
            None
        """
        self.current_images = images
        self.current_product = product_data
        
        # DynamoDB 제외 목록 초기화 (새 상품 로드 시)
        self.clear_excluded_images()
        
        # 도움말 버튼 활성화 (제품이 로드되면) => meta.json 정보 확인가능한 버튼 
        self.help_button.setEnabled(True)
        
        # 색상 정보 업데이트 (meta.json에서 color_info 읽어오기)
        product_id = product_data.get('product_id')
        if product_id:
            self.update_color_info_display(product_id)
        
        # 폴더별로 이미지 분류 (segment는 나중에 처리)
        for folder_name, tab_data in self.folder_tabs.items():
            folder_images = [img for img in images if img.get('folder') == folder_name]
            tab_data['images'] = folder_images
            tab_data['current_index'] = 0
            
            # segment 폴더는 로컬 이미지까지 로드한 후에 한 번만 업데이트
            if folder_name != 'segment':
                self.update_folder_display(folder_name)
        
        # 기존 로컬 Segment 이미지들도 로드(사용자가 이미지 뷰어에서 새로운 segment 이미지를 생성한 경우)
        self.load_existing_segment_images()
        
        # Segment 폴더 디스플레이 업데이트 (S3 + 로컬 이미지 모두 포함하여 한 번만)
        self.update_folder_display('segment')
        
        # 기본 탭을 segment로 설정
        self.set_default_tab_to_segment()
    
    def set_default_tab_to_segment(self):
        """기본 탭을 segment로 설정"""
        try:
            # segment 탭의 인덱스 찾기
            folder_names = list(self.folder_tabs.keys())
            if 'segment' in folder_names:
                segment_index = folder_names.index('segment')
                self.tab_widget.setCurrentIndex(segment_index)
                logger.debug("기본 탭을 segment로 설정")
            else:
                logger.warning("segment 탭을 찾을 수 없습니다.")
        except Exception as e:
            logger.error(f"기본 탭 설정 오류: {str(e)}")
    
    def switch_to_next_tab(self):
        """다음 탭으로 이동 (Tab키 처리)"""
        try:
            current_index = self.tab_widget.currentIndex()
            total_tabs = self.tab_widget.count()
            
            if total_tabs <= 1:
                return
            
            # 다음 탭 인덱스 계산 (마지막 탭에서는 첫 번째 탭으로)
            next_index = (current_index + 1) % total_tabs
            self.tab_widget.setCurrentIndex(next_index)
            
            # 현재 탭 이름 로그 (DEBUG 레벨로 변경)
            folder_names = list(self.folder_tabs.keys())
            if next_index < len(folder_names):
                logger.debug(f"탭 이동: {folder_names[next_index]}")
                
        except Exception as e:
            logger.error(f"탭 이동 오류: {str(e)}")
    
    def update_folder_display(self, folder_name: str):
        """
        폴더 디스플레이 업데이트 각 폴더명 별로 GridImageWidget 생성 => GridImageWidget 초기화시 ImageCache 클래스의 .get_image 호출) 
        args: 
            folder_name(str) : 폴더명 문자열 [detail, segment, summary, text] 중 1개 (중앙 패널의 탭 영역의 문자열)
        return:
            None
        """
        try:
            tab_data = self.folder_tabs[folder_name]
            images = tab_data['images']
            grid_layout = tab_data['grid_layout']
            
            # 기존 이미지 위젯들 안전하게 제거
            self.safe_cleanup_widgets(tab_data)
            
            self.clear_grid_layout(grid_layout)
            tab_data['image_widgets'] = []
            
            # 이미지 카운터 업데이트
            total_images = len(images)
            tab_data['image_counter'].setText(f"{total_images}개 이미지")
            
            # 이미지들을 그리드에 배치
            if total_images > 0:
                columns = 3  # 한 행에 3개 이미지
                for i, image_data in enumerate(images):
                    row = i // columns
                    col = i % columns
                    
                    try:
                        # 그리드 이미지 위젯 생성
                        image_widget = GridImageWidget(image_data, self.image_cache)
                        
                        # DynamoDB 제외 상태 설정 (text 폴더인 경우만)
                        if folder_name == 'text':
                            image_identifier = self.get_image_identifier(image_data)
                            is_excluded = image_identifier in self.excluded_from_dynamodb
                            image_widget.set_excluded_from_dynamodb(is_excluded)
                        
                        # 시그널 연결 - partial 함수 객체를 명시적으로 저장
                        try:
                            from functools import partial
                            clicked_slot = partial(self.on_image_selected, folder_name)
                            double_clicked_slot = self.open_image_viewer
                            
                            # 시그널 연결 (GridImageWidget 내부에서 튜플로 저장됨)
                            image_widget.connect_signals(clicked_slot, double_clicked_slot)
                            
                        except Exception as e:
                            logger.warning(f"이미지 위젯 시그널 연결 실패: {str(e)}")
                            # 위젯이 파괴된 경우 목록에서 제거
                            if hasattr(image_widget, '_is_destroyed') and image_widget._is_destroyed:
                                continue
                        
                        grid_layout.addWidget(image_widget, row, col)
                        tab_data['image_widgets'].append(image_widget)
                        
                    except Exception as e:
                        logger.error(f"이미지 위젯 생성 실패: {str(e)}")
                        continue
                
                # 그리드의 마지막에 스트레치 추가
                spacer = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)
                grid_layout.addItem(spacer, (total_images // columns) + 1, 0, 1, columns)
            else:
                # 이미지가 없는 경우 안내 메시지
                no_image_label = QLabel("이 폴더에 이미지가 없습니다")
                no_image_label.setAlignment(Qt.AlignCenter)
                no_image_label.setStyleSheet("color: #6c757d; background-color: #f8f9fa; font-size: 14px; padding: 50px; border-radius: 8px;")
                grid_layout.addWidget(no_image_label, 0, 0, 1, 3)
            
            # 선택 상태 초기화
            tab_data['selected_image_data'] = None
            tab_data['selected_info_label'].setText("이미지를 선택해주세요")
            
        except Exception as e:
            logger.error(f"폴더 디스플레이 업데이트 오류 {folder_name}: {str(e)}")
    
    def safe_cleanup_widgets(self, tab_data):
        """위젯들을 안전하게 정리 - 메모리 손상 방지 강화"""
        if 'image_widgets' not in tab_data:
            return
        
        # 위젯 목록을 복사하고 즉시 초기화하여 중복 처리 방지
        widgets_to_delete = tab_data['image_widgets'].copy()
        tab_data['image_widgets'] = []  # 참조를 즉시 제거하여 새롭게 추가될 위젯과 겹치지 않도록 함
        
        for widget in widgets_to_delete:
            try:
                if not widget:
                    continue
                
                # 위젯이 이미 파괴되었는지 확인
                if hasattr(widget, '_is_destroyed') and widget._is_destroyed:
                    continue
                
                # 위젯이 유효한 Qt 객체인지 확인
                if not hasattr(widget, 'isWidgetType') or not widget.isWidgetType():
                    continue
                
                # 위젯 정보 로깅 (cleanup 전에 미리 저장)
                widget_name = "unknown"
                try:
                    if hasattr(widget, 'image_data') and widget.image_data:
                        widget_name = widget.image_data.get('filename', 'unknown')
                except:
                    pass
                
                # 위젯 내부의 cleanup 로직 호출 (안전하게)
                try:
                    if hasattr(widget, 'perform_cleanup'):
                        widget.perform_cleanup()
                    elif hasattr(widget, 'cleanup'):
                        widget.cleanup()
                except Exception as cleanup_error:
                    logger.warning(f"위젯 cleanup 중 오류: {cleanup_error}")
                
                # 부모-자식 관계 안전하게 끊기
                try:
                    if hasattr(widget, 'parent') and widget.parent() is not None:
                        widget.setParent(None)
                except Exception as parent_error:
                    logger.warning(f"부모 관계 끊기 중 오류: {parent_error}")
                
                # Qt 이벤트 루프에서 안전하게 삭제 예약
                try:
                    if hasattr(widget, 'deleteLater'):
                        widget.deleteLater()
                except Exception as delete_error:
                    logger.warning(f"deleteLater 호출 중 오류: {delete_error}")
                
                logger.debug(f"위젯 정리 완료: {widget_name}")
                
            except Exception as e:
                logger.warning(f"MainImageViewer::safe_cleanup_widgets: 위젯 정리 중 오류: {str(e)}")
                continue
        
        # 가비지 컬렉션 강제 실행 (메모리 정리)
        import gc
        gc.collect()
    
    def clear_grid_layout(self, layout):
        """그리드 레이아웃 정리 - 레이아웃에서 위젯 제거만 담당"""
        try:
            while layout.count():
                child = layout.takeAt(0)
                if child.widget():
                    widget = child.widget()
                    try:
                        # 레이아웃에서만 제거 (실제 위젯 정리는 safe_cleanup_widgets에서 처리)
                        if widget.parent():
                            widget.setParent(None)
                        
                        # GridImageWidget인 경우 파괴 상태만 마킹
                        if isinstance(widget, GridImageWidget):
                            widget._is_destroyed = True
                        
                    except Exception as e:
                        logger.warning(f"레이아웃에서 위젯 제거 중 오류: {str(e)}")
                        continue
                elif child.spacerItem():
                    # 스페이서 아이템 제거
                    layout.removeItem(child.spacerItem())
                    
        except Exception as e:
            logger.error(f"그리드 레이아웃 정리 오류: {str(e)}")
    
    def on_image_selected(self, folder_name, image_data):
        """이미지 선택 처리 - 모드에 따라 바로 대표 이미지 설정"""
        tab_data = self.folder_tabs[folder_name]
        
        # 이전 선택 해제
        for widget in tab_data['image_widgets']:
            widget.set_selected(False)
        
        # 새로운 선택 설정
        for widget in tab_data['image_widgets']:
            if widget.image_data == image_data:
                widget.set_selected(True)
                break
        
        # 선택된 이미지 정보 업데이트
        tab_data['selected_image_data'] = image_data
        filename = image_data.get('filename', image_data.get('url', '').split('/')[-1])
        tab_data['selected_info_label'].setText(f"선택됨: {filename}")
        
        # 헤더의 이미지 정보도 업데이트
        self.image_info_label.setText(f"{folder_name}/{filename}")
        
        # 버튼 상태 업데이트
        self.update_button_states(folder_name, image_data)
        
        # DynamoDB 제외 모드가 활성화되어 있으면 제외 토글 실행
        if self.exclude_mode_active:
            self.toggle_image_exclusion(image_data)
            return
        
        # 선택된 모드가 있으면 바로 대표 이미지로 설정
        if self.selection_mode:
            self.set_as_representative_direct(image_data, self.selection_mode)
    
    def set_as_representative_direct(self, image_data, image_type):
        """모드에 따라 바로 대표 이미지로 설정"""
        if not self.representative_panel:
            logger.error("대표 이미지 패널 참조가 설정되지 않았습니다.")
            return
        
        # 시그널 발송 - 기존 코드와 동일하게 대표 이미지 패널로 전달
        self.representative_selected.emit(image_data, image_type)
        
        # 선택 모드 초기화 (선택 후 모드 해제)
        self.clear_selection_mode()
        
        # 성공 메시지 표시
        mode_names = {
            'model_wearing': '모델 착용',
            'front_cutout': '정면 누끼',
            'back_cutout': '후면 누끼',
            'color_variant': '제품 색상'
        }
        mode_name = mode_names.get(image_type, image_type)
        filename = image_data.get('filename', image_data.get('url', '').split('/')[-1])
        
        # 임시로 성공 메시지를 현재 모드 레이블에 표시
        self.current_mode_label.setText(f"✓ {mode_name} 이미지로 설정됨: {filename}")
        self.current_mode_label.setStyleSheet("color: #28a745; font-size: 11px; background-color: transparent; font-weight: bold;")
        
        # 3초 후 원래 메시지로 복원
        QTimer.singleShot(3000, self.restore_default_mode_message)
    
    def open_image_viewer(self, image_data: Dict[str, Any]):
        """이미지 뷰어 열기"""
        try:
            # URL 기반 이미지 뷰어 다이얼로그 생성 (현재 제품 정보와 AWS 매니저 전달)
            viewer_dialog = UrlImageViewerDialog(
                image_data, 
                self.image_cache, 
                self.current_product,
                self.aws_manager,
                self
            )
            
            # Segment 이미지 생성 시그널 연결
            viewer_dialog.segment_image_created.connect(self._on_segment_image_created)
            
            viewer_dialog.exec()
        except Exception as e:
            logger.error(f"이미지 뷰어 열기 오류: {str(e)}")
            # 에러 메시지 표시
            self.current_mode_label.setText(f"❌ 이미지 뷰어 오류: {str(e)}")
            self.current_mode_label.setStyleSheet("color: #dc3545; font-size: 11px; background-color: transparent; font-weight: bold;")
            
            # 3초 후 원래 메시지로 복원
            QTimer.singleShot(3000, self.restore_default_mode_message)

    def _on_segment_image_created(self, new_image_data: Dict[str, Any]):
        """새로운 Segment 이미지가 생성되었을 때 처리"""
        try:
            # Segment 폴더 탭 데이터 가져오기
            if 'segment' not in self.folder_tabs:
                logger.warning("Segment 폴더 탭이 없습니다.")
                return
            
            segment_tab_data = self.folder_tabs['segment']
            
            # 새 이미지를 segment 폴더 이미지 목록에 추가
            segment_tab_data['images'].append(new_image_data)
            
            # 전체 이미지 목록에도 추가
            self.current_images.append(new_image_data)
            
            # Segment 폴더 디스플레이 업데이트
            self.update_folder_display('segment')
            
            # Segment 탭으로 자동 전환
            segment_tab_index = list(self.folder_tabs.keys()).index('segment')
            self.tab_widget.setCurrentIndex(segment_tab_index)
            
        except Exception as e:
            logger.error(f"Segment 이미지 추가 오류: {str(e)}")

    def load_existing_segment_images(self):
        """
            기존 로컬 Segment 이미지들을 로드
            - 사용자가 이미지 뷰어에서 새로운 segment 이미지를 생성한 경우 캐시 디렉토리/{product_id}/segment/ 디렉토리에 이미지 저장됨
            - 만약 해당 폴더내에 이미지가 존재하는 경우 로드
        """
        try:
            if not self.current_product:
                return
            
            product_id = self.current_product.get('product_id', '')
            if not product_id:
                return
            
            # 캐시 디렉토리에서 제품별 segment 폴더 확인
            if self.image_cache and hasattr(self.image_cache, 'cache_dir'):
                base_dir = Path(self.image_cache.cache_dir)
            else:
                base_dir = Path.home() / '.cache' / 'ai_dataset_curation' / 'product_images'
            
            # 제품별 segment 디렉토리: {cache_dir}/{product_id}/segment/
            product_segment_dir = base_dir / product_id / 'segment'
            
            if not product_segment_dir.exists():
                # 기존 방식의 segments 폴더도 확인 (하위 호환성)
                legacy_segments_dir = base_dir / 'segments'
                if legacy_segments_dir.exists():
                    self._load_legacy_segment_images(legacy_segments_dir, product_id)
                return
            
            segment_images = []
            
            # .jpg, .png 파일들만 확인
            for img_file in product_segment_dir.glob('*'):
                if img_file.suffix.lower() in ['.jpg', '.jpeg', '.png']:
                    try:
                        # 이미지 데이터 생성
                        file_size = img_file.stat().st_size
                        
                        # 이미지 크기 확인
                        try:
                            with Image.open(img_file) as pil_img:
                                width, height = pil_img.size
                        except Exception:
                            width, height = 512, 512
                        
                        file_url = f"file://{img_file.absolute()}"
                        
                        # 표시용 이름 생성
                        display_name = self._generate_display_name_for_existing(img_file.name)
                        
                        # S3 키 형식 생성
                        main_category = self.current_product.get('main_category', '')
                        sub_category = self.current_product.get('sub_category', '')
                        s3_key = f"segment/{img_file.name}"
                        if all([main_category, sub_category, product_id]):
                            s3_key = f"{main_category}/{sub_category}/{product_id}/segment/{img_file.name}"
                        
                        image_data = {
                            'key': s3_key,
                            'url': file_url,
                            'folder': 'segment',
                            'filename': img_file.name,
                            'local_path': str(img_file),
                            'is_local_segment': True,
                            'file_size': file_size,
                            'dimensions': f"{width}x{height}",
                            'product_id': product_id,
                            'display_name': display_name,
                            'cached': True,
                            'cache_path': str(img_file)
                        }
                        
                        segment_images.append(image_data)
                        
                    except Exception as e:
                        logger.warning(f"Segment 이미지 로드 실패 {img_file}: {str(e)}")
            
            # segment 폴더에 기존 이미지들 추가
            if segment_images and 'segment' in self.folder_tabs:
                segment_tab_data = self.folder_tabs['segment']
                
                # 기존 이미지들과 중복 제거 (파일명 기준)
                existing_filenames = {img['filename'] for img in segment_tab_data['images']}
                new_images = [img for img in segment_images if img['filename'] not in existing_filenames]
                
                segment_tab_data['images'].extend(new_images)
                self.current_images.extend(new_images)
                
                logger.debug(f"제품별 segment 폴더에서 {len(new_images)}개 이미지 로드: {product_id}")
                
        except Exception as e:
            logger.error(f"기존 Segment 이미지 로드 오류: {str(e)}")

    def _load_legacy_segment_images(self, legacy_segments_dir: Path, product_id: str):
        """기존 방식의 segments 폴더에서 이미지 로드 (하위 호환성)"""
        try:
            segment_images = []
            
            # .jpg 파일들만 확인하고 제품 ID와 관련된 것만 로드
            for img_file in legacy_segments_dir.glob('*.jpg'):
                try:
                    # 파일명 패턴 확인 (제품 ID 기반)
                    should_include = False
                    
                    # 새로운 형식: PROD_seg_001.jpg (제품 ID 기반)
                    if product_id and img_file.name.startswith(f"{product_id[:8]}_seg_"):
                        should_include = True
                    # 기존 형식: 제품 ID가 파일명에 포함된 경우
                    elif product_id and product_id in img_file.name:
                        should_include = True
                    # seg_로 시작하는 일반 형식 (조심스럽게 포함)
                    elif img_file.name.startswith('seg_'):
                        should_include = True
                    
                    if should_include:
                        # 이미지 데이터 생성
                        file_size = img_file.stat().st_size
                        
                        # 이미지 크기 확인
                        try:
                            with Image.open(img_file) as pil_img:
                                width, height = pil_img.size
                        except Exception:
                            width, height = 512, 512
                        
                        file_url = f"file://{img_file.absolute()}"
                        
                        # 표시용 이름 생성
                        display_name = self._generate_display_name_for_existing(img_file.name)
                        
                        image_data = {
                            'key': f"segments/{img_file.name}",  # 기존 키 형식 유지
                            'url': file_url,
                            'folder': 'segment',
                            'filename': img_file.name,
                            'local_path': str(img_file),
                            'is_local_segment': True,
                            'file_size': file_size,
                            'dimensions': f"{width}x{height}",
                            'product_id': product_id,
                            'display_name': display_name,
                            'is_legacy': True  # 기존 방식임을 표시
                        }
                        
                        segment_images.append(image_data)
                        
                except Exception as e:
                    logger.warning(f"Legacy Segment 이미지 로드 실패 {img_file}: {str(e)}")
            
            # segment 폴더에 기존 이미지들 추가
            if segment_images and 'segment' in self.folder_tabs:
                segment_tab_data = self.folder_tabs['segment']
                
                # 기존 이미지들과 중복 제거 (파일명 기준)
                existing_filenames = {img['filename'] for img in segment_tab_data['images']}
                new_images = [img for img in segment_images if img['filename'] not in existing_filenames]
                
                segment_tab_data['images'].extend(new_images)
                self.current_images.extend(new_images)
                
                logger.debug(f"Legacy segments 폴더에서 {len(new_images)}개 이미지 로드: {product_id}")
                
        except Exception as e:
            logger.error(f"Legacy Segment 이미지 로드 오류: {str(e)}")

    def _generate_display_name_for_existing(self, filename: str) -> str:
        """기존 파일의 표시용 이름 생성"""
        try:
            # seg_001.jpg -> "Segment #1"
            # PROD123_seg_002.jpg -> "PROD123 Segment #2"
            # old_format_segment_123_abc.jpg -> "old_format..."
            
            name_without_ext = os.path.splitext(filename)[0]
            parts = name_without_ext.split('_')
            
            # 새로운 형식: PREFIX_seg_NUMBER
            if len(parts) >= 3 and parts[-2] == 'seg':
                product_part = '_'.join(parts[:-2]) if len(parts) > 3 else ""
                number_part = parts[-1]
                
                if number_part.isdigit():
                    number = int(number_part)
                    if product_part:
                        return f"{product_part} Segment #{number}"
                    else:
                        return f"Segment #{number}"
            
            # 기존 형식: seg_TIMESTAMP
            if filename.startswith('seg_') and len(parts) == 2:
                timestamp_part = parts[1]
                if timestamp_part.isdigit():
                    # 타임스탬프의 마지막 3자리를 번호로 사용
                    number = int(timestamp_part[-3:]) if len(timestamp_part) >= 3 else int(timestamp_part)
                    return f"Segment #{number}"
            
            # 매우 긴 파일명 줄이기
            if len(filename) > 30:
                return f"{filename[:25]}..."
            
            # 폴백: 원본 파일명에서 확장자 제거
            return name_without_ext
            
        except Exception:
            return os.path.splitext(filename)[0]

    def get_current_selected_image(self):
        """현재 선택된 이미지 데이터 반환"""
        current_folder = list(self.folder_tabs.keys())[self.tab_widget.currentIndex()]
        tab_data = self.folder_tabs.get(current_folder)
        if tab_data:
            return tab_data.get('selected_image_data')
        return None
    

    
    def open_image_viewer_button_clicked(self):
        """이미지 뷰어 버튼 클릭 처리"""
        selected_image = self.get_current_selected_image()
        if selected_image:
            self.open_image_viewer(selected_image)
        else:
            # 선택된 이미지가 없으면 안내 메시지
            self.show_status_message("❌ 먼저 이미지를 선택해주세요", error=True)
    
    def show_meta_json(self):
        """로컬 캐시에서 meta.json 읽어서 팝업 표시"""
        if not self.current_product:
            logger.warning("현재 제품 정보가 없습니다.")
            return
        
        if not self.image_cache:
            logger.error("이미지 캐시가 설정되지 않았습니다.")
            return
        
        product_id = self.current_product.get('product_id')
        if not product_id:
            logger.error("제품 ID가 없습니다.")
            return
        
        # 버튼 임시 비활성화
        self.help_button.setEnabled(False)
        self.help_button.setText("📋 로딩중...")
        
        try:
            # 로컬 캐시에서 meta.json 읽기
            meta_data = self.image_cache.get_product_meta_json(product_id)
            
            if meta_data:
                self.on_meta_json_loaded(meta_data)
            else:
                self.on_meta_json_error("캐시된 meta.json 파일을 찾을 수 없습니다.")
                
        except Exception as e:
            logger.error(f"meta.json 읽기 오류: {e}")
            self.on_meta_json_error(f"meta.json 읽기 오류: {str(e)}")
    
    def on_meta_json_loaded(self, meta_data):
        """meta.json 로드 완료 처리"""
        try:
            # 팝업 다이얼로그 생성 및 표시
            product_id = self.current_product.get('product_id', 'Unknown')
            dialog = MetaJsonDialog(meta_data, product_id, self)
            dialog.exec()
            
        finally:
            # 버튼 복원
            self.help_button.setEnabled(True)
            self.help_button.setText("📋 상품 정보")
    
    def on_meta_json_error(self, error_message):
        """meta.json 로드 오류 처리"""
        logger.error(f"meta.json 로드 오류: {error_message}")
        
        # 간단한 오류 메시지 표시
        self.current_mode_label.setText(f"❌ 오류: {error_message}")
        self.current_mode_label.setStyleSheet("color: #dc3545; font-size: 11px; background-color: transparent; font-weight: bold;")
        
        # 3초 후 원래 메시지로 복원
        QTimer.singleShot(3000, self.restore_default_mode_message)
        
        # 버튼 복원
        self.help_button.setEnabled(True)
        self.help_button.setText("📋 상품 정보")

    def clear(self):
        """뷰어 초기화"""
        try:
            self.current_images = []
            self.current_product = None
            
            # 이동 히스토리 및 대기 목록 초기화
            self.move_history.clear()
            self.pending_moves.clear()
            
            # 도움말 버튼 비활성화
            self.help_button.setEnabled(False)
            
            # 색상 정보 숨김
            self.color_info_label.setVisible(False)
            
            # 선택 모드 초기화
            self.clear_selection_mode()
            
            # 워커 쓰레드 정리 (curation_worker만)
            if self.curation_worker:
                if self.curation_worker.isRunning():
                    self.curation_worker.cancel()  # 취소 신호 전송
                    self.curation_worker.quit()
                    if not self.curation_worker.wait(3000):  # 3초 대기
                        self.curation_worker.terminate()  # 강제 종료
                        self.curation_worker.wait()
                self.curation_worker.deleteLater()
                self.curation_worker = None
            
            # 모든 폴더 탭의 위젯들 안전하게 정리 (순서 중요)
            for folder_name, tab_data in self.folder_tabs.items():
                # 1. 먼저 위젯들을 안전하게 정리
                self.safe_cleanup_widgets(tab_data)
                
                # 2. 데이터 초기화
                tab_data['images'] = []
                tab_data['selected_image_data'] = None
                
                # 3. 레이아웃 정리
                self.clear_grid_layout(tab_data['grid_layout'])
                
                # 4. 디스플레이 업데이트
                self.update_folder_display(folder_name)
            
            self.image_info_label.setText("이미지를 선택해주세요")
            
            # 모든 버튼 상태 초기화
            self.move_to_text_btn.setEnabled(False)
            self.undo_btn.setEnabled(False)
            
            # 버튼 상태 검증 및 복구
            QTimer.singleShot(100, self.verify_and_repair_buttons)
            
            logger.debug("MainImageViewer 초기화 완료")
            
        except Exception as e:
            logger.error(f"뷰어 초기화 오류: {str(e)}")
    
    def mousePressEvent(self, event):
        """마우스 클릭 시 포커스 설정"""
        try:
            # 강화된 포커스 설정
            self.ensure_keyboard_focus()
            super().mousePressEvent(event)
        except Exception as e:
            logger.error(f"마우스 이벤트 처리 오류: {str(e)}")
    
    def showEvent(self, event):
        """위젯이 표시될 때 포커스 설정"""
        try:
            super().showEvent(event)
            # 위젯이 완전히 표시된 후 포커스 설정 (다중 단계로 확실히)
            QTimer.singleShot(50, self.ensure_keyboard_focus)
            QTimer.singleShot(100, self.ensure_keyboard_focus)
            QTimer.singleShot(200, self.ensure_keyboard_focus)
        except Exception as e:
            logger.error(f"위젯 표시 이벤트 처리 오류: {str(e)}")
    
    def eventFilter(self, obj, event):
        """이벤트 필터 - 키보드 이벤트를 우선 처리"""
        if event.type() == event.Type.KeyPress:
            # 키보드 이벤트를 직접 처리
            self.keyPressEvent(event)
            if event.isAccepted():
                return True
        return super().eventFilter(obj, event)
    
    def keyPressEvent(self, event: QKeyEvent):
        """키보드 이벤트 처리"""
        try:
            # 키보드 이벤트 처리 전에 포커스 확인 및 강화
            if not self.hasFocus():
                self.ensure_keyboard_focus()
            
            # Tab: 다음 탭으로 이동
            if event.key() == Qt.Key_Tab:
                self.switch_to_next_tab()
                event.accept()
                return
            
            # Ctrl+Z: 되돌리기
            elif event.key() == Qt.Key_Z and event.modifiers() == Qt.ControlModifier:
                if self.undo_btn.isEnabled():
                    self.undo_last_move()
                    event.accept()
                    return
            
            # Ctrl+R: 버튼 강제 복구
            elif event.key() == Qt.Key_R and event.modifiers() == Qt.ControlModifier:
                logger.info("Ctrl+R 눌림 - 버튼 강제 복구")
                self.force_button_repair()
                event.accept()
                return
            
            # M/m: Text 폴더로 이동 (segment 폴더에서만)
            elif event.key() == Qt.Key_M:
                if self.move_to_text_btn.isEnabled():
                    self.move_image_to_text()
                    event.accept()
                    return
                else:
                    # 버튼이 비활성화된 경우 안내 메시지
                    self.show_status_message("❌ Segment 폴더의 이미지를 선택해주세요", error=True)
                    event.accept()
                    return
            
            # 숫자키 1-4: 대표 이미지 모드 선택 (직접 호출)
            elif event.key() == Qt.Key_1:
                logger.debug("키보드 1번 눌림 - 모델 선택")
                self.set_selection_mode('model_wearing')
                event.accept()
                return
            elif event.key() == Qt.Key_2:
                logger.debug("키보드 2번 눌림 - 정면 선택")
                self.set_selection_mode('front_cutout')
                event.accept()
                return
            elif event.key() == Qt.Key_3:
                logger.debug("키보드 3번 눌림 - 후면 선택")
                self.set_selection_mode('back_cutout')
                event.accept()
                return
            elif event.key() == Qt.Key_4:
                logger.debug("키보드 4번 눌림 - 색상 선택")
                self.set_selection_mode('color_variant')
                event.accept()
                return
            elif event.key() == Qt.Key_5:
                logger.debug("키보드 5번 눌림 - DynamoDB 제외 모드")
                current_folder = self.get_current_folder_name()
                if current_folder == 'text':
                    self.set_exclude_mode(not self.exclude_mode_active)
                else:
                    self.show_status_message("❌ text 폴더에서만 DynamoDB 제외 모드를 사용할 수 있습니다", error=True)
                event.accept()
                return
            
            # ESC: 선택 모드 취소
            elif event.key() == Qt.Key_Escape:
                logger.debug("키보드 ESC 눌림 - 선택 취소")
                self.clear_selection_mode()
                event.accept()
                return
            
            # V: 이미지 뷰어 열기
            elif event.key() == Qt.Key_V:
                self.open_image_viewer_button_clicked()
                event.accept()
                return
            
        except Exception as e:
            logger.error(f"키보드 이벤트 처리 오류: {str(e)}")
            # 오류 발생 시 버튼 복구 시도
            QTimer.singleShot(100, self.force_button_repair)
        
        # 처리되지 않은 키는 부모 클래스로 전달
        super().keyPressEvent(event)

    def get_pending_moves(self):
        """대기 중인 S3 이동 목록 반환"""
        return self.pending_moves.copy()
    
    def clear_pending_moves(self):
        """대기 중인 S3 이동 목록 초기화"""
        self.pending_moves.clear()
        self.move_history.clear()

    def verify_and_repair_buttons(self):
        """버튼 상태 검증 및 복구"""
        try:
            logger.info("버튼 상태 검증 시작")
            
            # 모드 버튼들 검증
            for mode_key, button in self.mode_buttons.items():
                if not button:
                    logger.warning(f"버튼이 None입니다: {mode_key}")
                    continue
                    
                # 시그널 연결 상태 확인
                receivers = button.receivers(button.clicked)
                logger.info(f"버튼 {mode_key}의 시그널 수신자 수: {receivers}")
                
                if receivers == 0:
                    logger.warning(f"버튼 {mode_key}의 시그널 연결이 해제됨 - 재연결")
                    # 시그널 재연결
                    button.clicked.disconnect()  # 기존 연결 모두 해제
                    
                    # Lambda 함수 클로저 문제 해결: 함수 팩토리 패턴 사용
                    def create_mode_click_handler(mode):
                        return lambda checked: self.set_selection_mode(mode)
                    
                    button.clicked.connect(create_mode_click_handler(mode_key))
            
            logger.info("버튼 상태 검증 완료")
            
        except Exception as e:
            logger.error(f"버튼 상태 검증 오류: {str(e)}")

    def force_button_repair(self):
        """강제로 버튼 복구"""
        try:
            logger.info("강제 버튼 복구 시작")
            
            # 모든 시그널 연결 해제
            for button in self.mode_buttons.values():
                if button:
                    try:
                        button.clicked.disconnect()
                    except:
                        pass  # 이미 연결이 해제된 경우 무시
            
            # 시그널 재연결
            for mode_key, button in self.mode_buttons.items():
                if button:
                    # Lambda 함수 클로저 문제 해결: 함수 팩토리 패턴 사용
                    def create_mode_click_handler(mode):
                        return lambda checked: self.set_selection_mode(mode)
                    
                    button.clicked.connect(create_mode_click_handler(mode_key))
            
            logger.info("강제 버튼 복구 완료")
            
        except Exception as e:
            logger.error(f"강제 버튼 복구 오류: {str(e)}")
    
    # =============================================================================
    # DynamoDB 제외 기능 관련 메서드들
    # =============================================================================
    
    def get_image_identifier(self, image_data: dict) -> str:
        """이미지의 고유 식별자를 생성"""
        product_id = self.current_product.get('product_id', 'unknown') if self.current_product else 'unknown'
        folder = image_data.get('folder', 'unknown')
        filename = image_data.get('filename', 'unknown')
        return f"{product_id}:{folder}:{filename}"
    
    def is_image_in_text_folder(self, image_data: dict) -> bool:
        """이미지가 text 폴더에 있는지 확인"""
        return image_data.get('folder') == 'text'
    
    def get_current_folder_name(self) -> str:
        """현재 선택된 탭의 폴더명을 반환"""
        if hasattr(self, 'tab_widget') and self.tab_widget:
            current_index = self.tab_widget.currentIndex()
            folder_names = list(self.folder_tabs.keys())
            if 0 <= current_index < len(folder_names):
                return folder_names[current_index]
        return 'unknown'
    
    def toggle_image_exclusion(self, image_data: dict):
        """이미지의 DynamoDB 제외 상태 토글 (text 폴더만)"""
        if not self.is_image_in_text_folder(image_data):
            self.show_status_message("❌ text 폴더의 이미지만 제외할 수 있습니다", error=True)
            return
        
        image_key = self.get_image_identifier(image_data)
        if image_key in self.excluded_from_dynamodb:
            self.excluded_from_dynamodb.remove(image_key)
            logger.info(f"DynamoDB 제외 해제: {image_data.get('filename')}")
            self.show_status_message(f"✅ DynamoDB 제외 해제: {image_data.get('filename')}")
        else:
            self.excluded_from_dynamodb.add(image_key)
            logger.info(f"DynamoDB 제외 추가: {image_data.get('filename')}")
            self.show_status_message(f"🚫 DynamoDB 제외 추가: {image_data.get('filename')}")
        
        # 즉시 시각적 업데이트 - text 탭의 모든 이미지 위젯 업데이트
        if 'text' in self.folder_tabs:
            text_tab_data = self.folder_tabs['text']
            for widget in text_tab_data.get('image_widgets', []):
                if hasattr(widget, 'set_excluded_from_dynamodb') and hasattr(widget, 'image_data'):
                    widget_image_key = self.get_image_identifier(widget.image_data)
                    is_excluded = widget_image_key in self.excluded_from_dynamodb
                    widget.set_excluded_from_dynamodb(is_excluded)
        
        # 시각적 업데이트
        self.update_exclude_status_display()
    
    def update_exclude_status_display(self):
        """제외 상태 표시 업데이트"""
        excluded_count = len(self.excluded_from_dynamodb)
        if excluded_count > 0:
            status_text = f"DynamoDB 제외 모드: {excluded_count}개 파일 제외됨"
            if hasattr(self, 'current_mode_label'):
                self.current_mode_label.setText(status_text)
                self.current_mode_label.setStyleSheet("color: #dc3545; font-size: 11px; background-color: transparent; font-weight: bold;")
        else:
            if hasattr(self, 'current_mode_label') and not self.exclude_mode_active:
                self.restore_default_mode_message()
    
    def set_exclude_mode(self, active: bool):
        """DynamoDB 제외 모드 설정/해제"""
        current_folder = self.get_current_folder_name()
        if active and current_folder != 'text':
            self.show_status_message("❌ text 폴더에서만 DynamoDB 제외 모드를 사용할 수 있습니다", error=True)
            return
        
        self.exclude_mode_active = active
        
        if active:
            # 제외 모드 활성화
            if hasattr(self, 'current_mode_label'):
                self.current_mode_label.setText("DynamoDB 제외 모드: 이미지를 클릭하여 DynamoDB 업데이트에서 제외하세요")
                self.current_mode_label.setStyleSheet("color: #dc3545; font-size: 11px; background-color: transparent; font-weight: bold;")
            
            # 모든 기존 모드 버튼 해제
            for btn in self.mode_buttons.values():
                if btn and btn.isCheckable():
                    btn.setChecked(False)
            
            # 제외 모드 버튼 활성화 (있다면)
            if 'exclude_from_dynamodb' in self.mode_buttons:
                exclude_btn = self.mode_buttons['exclude_from_dynamodb']
                if exclude_btn and exclude_btn.isCheckable():
                    exclude_btn.setChecked(True)
            
            logger.info("DynamoDB 제외 모드 활성화")
        else:
            # 제외 모드 비활성화
            self.restore_default_mode_message()
            
            # 제외 모드 버튼 비활성화 (있다면)
            if 'exclude_from_dynamodb' in self.mode_buttons:
                exclude_btn = self.mode_buttons['exclude_from_dynamodb']
                if exclude_btn and exclude_btn.isCheckable():
                    exclude_btn.setChecked(False)
            
            logger.info("DynamoDB 제외 모드 비활성화")
    
    def clear_excluded_images(self):
        """제외된 이미지 목록 초기화 (새 상품 로드 시)"""
        self.excluded_from_dynamodb.clear()
        self.exclude_mode_active = False
        
        # 모든 이미지 위젯의 제외 상태 초기화
        for folder_name, tab_data in self.folder_tabs.items():
            for widget in tab_data.get('image_widgets', []):
                if hasattr(widget, 'set_excluded_from_dynamodb'):
                    widget.set_excluded_from_dynamodb(False)
        
        logger.info("DynamoDB 제외 목록 초기화")
    
    def get_excluded_text_filenames(self) -> list[str]:
        """text 폴더에서 DynamoDB 업데이트에서 제외할 파일명들을 반환"""
        excluded_filenames = []
        
        for image_identifier in self.excluded_from_dynamodb:
            # image_identifier 형식: "product_id:folder:filename"
            if ':text:' in image_identifier:  # text 폴더인 경우만
                filename = image_identifier.split(':')[-1]  # 마지막 부분이 파일명
                excluded_filenames.append(filename)
        
        return excluded_filenames

    def open_musinsa_page(self):
        """현재 상품의 무신사 웹페이지 열기"""
        if not self.current_product:
            self.show_status_message("❌ 상품 정보가 없습니다", error=True)
            return
            
        product_id = self.current_product.get('product_id')
        if not product_id:
            self.show_status_message("❌ 상품 ID를 찾을 수 없습니다", error=True)
            return
            
        url = f"https://www.musinsa.com/products/{product_id}"
        try:
            webbrowser.open(url)
            self.show_status_message(f"✅ 무신사 상품 페이지 열기: {product_id}")
        except Exception as e:
            logger.error(f"무신사 페이지 열기 오류: {str(e)}")
            self.show_status_message("❌ 무신사 페이지 열기 실패", error=True)