#!/usr/bin/env python3
"""
AWS 통신 중앙 관리 모듈
S3와 DynamoDB와의 모든 통신을 담당하는 핵심 모듈입니다.
"""

import boto3
import json
import os
from datetime import datetime, timezone
from typing import Any, List, Dict
from botocore.exceptions import ClientError
from pathlib import Path
import mimetypes
import logging
import botocore.config
# 로깅 설정
logger = logging.getLogger(__name__)


class AWSManager:
    """AWS 리소스 관리 클래스"""
    
    def __init__(self, region_name: str = 'ap-northeast-2', profile_name: str = None):
        """
        AWS Manager 초기화
        
        Args:
            region_name: AWS 리전명
            profile_name: AWS 프로필명 (None이면 기본 프로필)
        """
        config = botocore.config.Config(
            max_pool_connections=50,  # 기본 10 → 50으로 증가
            retries={'max_attempts': 3}
        )
        self.region_name = region_name
        self.profile_name = profile_name
    
        
        # 클라이언트 초기화
        self.s3_client = boto3.client('s3', region_name=region_name, config=config)
        self.dynamodb_client = boto3.client('dynamodb', region_name=region_name, config=config)
        self.sts_client = boto3.client('sts', region_name=region_name, config=config)
        
        # 설정 로드
        self.config = self._load_config()
        self.bucket_name = self.config.get('s3', {}).get('bucket_name', 'sw-fashion-image-data')
        self.table_name = self.config.get('dynamodb', {}).get('table_name', 'ProductAssets')
        
        if not self.bucket_name:
            raise ValueError("S3 버킷 이름이 설정되지 않았습니다. config.json을 확인해주세요.")
        
        logger.info(f"AWS Manager 초기화 완료 - 리전: {region_name}, 버킷: {self.bucket_name}, 테이블: {self.table_name}")
    
    def _load_config(self) -> dict[str, Any]:
        """설정 파일을 로드합니다."""
        # boto3/config.json 파일 찾기
        #TODO : 파일경로 주의 필요
        config_path = Path(__file__).parent / 'config.json'
        
        # 현재 디렉토리에서 boto3/config.json도 시도
        if not config_path.exists():
            config_path = Path('boto3') / 'config.json'
        
        # 현재 디렉토리의 config.json도 시도
        if not config_path.exists():
            config_path = Path('config.json')
            
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.warning(f"설정 파일을 찾을 수 없습니다: {config_path}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"설정 파일 JSON 형식 오류: {e}")
            return {}
    
    def _get_current_timestamp(self) -> str:
        """현재 시각을 ISO 8601 형식으로 반환합니다."""
        return datetime.now(timezone.utc).isoformat()
    
    def _convert_dynamodb_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """
        DynamoDB client 응답 아이템을 일반 딕셔너리로 변환합니다.
        
        Args:
            item: DynamoDB에서 쿼리 반환결과인 Item 키에 대한 데이터 (타입 정보 포함)
                example : {'속성이름': {'데이터타입': '값'}, ...}
            
        Returns:
            dict: 변환된 일반 딕셔너리
        """
        converted = {}
        for key, value in item.items():
            if 'S' in value:  # String
                converted[key] = value['S']
            elif 'N' in value:  # Number
                # 정수로 변환 시도, 실패하면 float
                try:
                    converted[key] = int(value['N'])
                except ValueError:
                    converted[key] = float(value['N'])
            elif 'SS' in value:  # String Set
                converted[key] = value['SS']
            elif 'NS' in value:  # Number Set
                converted[key] = [int(n) for n in value['NS']]
            elif 'BOOL' in value:  # Boolean
                converted[key] = value['BOOL']
            elif 'NULL' in value:  # Null
                converted[key] = None
            elif 'M' in value:  # Map
                converted[key] = self._convert_dynamodb_item(value['M'])
            elif 'L' in value:  # List
                # List 내부 아이템들을 변환
                converted_list = []
                for list_item in value['L']:
                    if 'S' in list_item:  # String 아이템
                        converted_list.append(list_item['S'])
                    else:
                        # 다른 타입의 아이템은 재귀 변환
                        converted_list.append(self._convert_dynamodb_item({'item': list_item})['item'])
                converted[key] = converted_list
            else:
                # 알 수 없는 타입은 그대로 유지
                converted[key] = value
        
        # JSON 문자열 필드들 파싱
        # json_fields = ['product_info', 'representative_assets']
        # for field in json_fields:
        #     if field in converted and isinstance(converted[field], str):
        #         try:
        #             converted[field] = json.loads(converted[field])
        #         except json.JSONDecodeError:
        #             pass
        
        # UI 호환성을 위한 필드 매핑
        # DynamoDB의 curation_status를 UI에서 사용하는 current_status로 매핑
        if 'curation_status' in converted:
            converted['current_status'] = converted['curation_status']
        
        # # 업데이트 시간 필드 매핑
        # if 'curation_updated_at' in converted:
        #     converted['last_updated_at'] = converted['curation_updated_at']
                
        return converted
    
    def _get_s3_object_key(self, main_category: str, sub_category: int, product_id: str, relative_path: str) -> str:
        """
        S3 객체 키를 생성합니다.
        
        Args:
            sub_category: 서브 카테고리 ID
            product_id: 제품 ID
            relative_path: 상대 경로 (예: 'detail/0.jpg', 'meta.json')
            
        Returns:
            str: S3 객체 키 (예: 'main_category/sub_category/product_id/relative_path')
        """
        return f"{main_category}/{sub_category}/{product_id}/{relative_path}"
    
    # =============================================================================
    # S3 관련 함수들
    # =============================================================================
    
    def upload_file_to_s3(self, local_file_path: str, s3_key: str, 
                          metadata: dict[str, str]|None = None,
                          tags: dict[str, str]|None = None) -> bool:
        """
        로컬 파일을 S3에 업로드합니다.
        
        Args:
            local_file_path: 로컬 파일 경로
            s3_key: S3 객체 키
            metadata: 파일 메타데이터
            tags: S3 객체 태그
            
        Returns:
            bool: 업로드 성공 여부
        """
        try:
            # MIME 타입 추론
            content_type, _ = mimetypes.guess_type(local_file_path)
            if content_type is None:
                content_type = 'binary/octet-stream'
            
            extra_args = {
                'ContentType': content_type
            }
            
            # 메타데이터 추가
            if metadata:
                extra_args['Metadata'] = metadata
            
            # 파일 업로드
            self.s3_client.upload_file(
                local_file_path,
                self.bucket_name,
                s3_key,
                ExtraArgs=extra_args
            )
            
            # 태그 추가 (별도 API 호출 필요)
            if tags:
                self.tag_s3_object(s3_key, tags)
            
            logger.info(f"S3 업로드 성공: {s3_key}")
            return True
            
        except ClientError as e:
            logger.error(f"S3 업로드 실패 {s3_key}: {e}")
            return False
        except Exception as e:
            logger.error(f"S3 업로드 중 예상치 못한 오류 {s3_key}: {e}")
            return False
    

    
    def tag_s3_object(self, s3_key: str, tags: dict[str, str]) -> bool:
        """
        S3 객체에 태그를 지정합니다.
        
        Args:
            s3_key: S3 객체 키
            tags: 태그 딕셔너리
            
        Returns:
            bool: 태그 지정 성공 여부
        """
        try:
            tag_set = [{'Key': k, 'Value': v} for k, v in tags.items()]
            
            self.s3_client.put_object_tagging(
                Bucket=self.bucket_name,
                Key=s3_key,
                Tagging={'TagSet': tag_set}
            )
            
            logger.info(f"S3 객체 태그 지정 성공: {s3_key}")
            return True
            
        except ClientError as e:
            logger.error(f"S3 객체 태그 지정 실패 {s3_key}: {e}")
            return False
    
    def move_s3_object(self, source_key: str, dest_key: str) -> bool:
        """
        S3 객체를 이동합니다 (복사 후 삭제).
        
        Args:
            source_key: 원본 객체 키
            dest_key: 대상 객체 키
            
        Returns:
            bool: 이동 성공 여부
        """
        try:
            # 객체 복사
            copy_source = {'Bucket': self.bucket_name, 'Key': source_key}
            self.s3_client.copy_object(
                CopySource=copy_source,
                Bucket=self.bucket_name,
                Key=dest_key
            )
            
            # 원본 객체 삭제
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=source_key)
            
            logger.info(f"S3 객체 이동 성공: {source_key} -> {dest_key}")
            return True
            
        except ClientError as e:
            logger.error(f"S3 객체 이동 실패 {source_key} -> {dest_key}: {e}")
            return False
    
    def batch_move_s3_objects(self, move_operations: list[tuple[str, str]]) -> dict[str, bool]:
        """
        여러 S3 객체를 배치로 이동합니다.
        
        Args:
            move_operations: 이동 작업 목록 [(source_key, dest_key), ...]
            
        Returns:
            Dict[str, bool]: 각 이동 작업의 성공/실패 결과 {source_key: success}
        """
        results = {}
        
        for source_key, dest_key in move_operations:
            try:
                success = self.move_s3_object(source_key, dest_key)
                results[source_key] = success
                
                if success:
                    logger.info(f"배치 이동 성공: {source_key} -> {dest_key}")
                else:
                    logger.error(f"배치 이동 실패: {source_key} -> {dest_key}")
                    
            except Exception as e:
                logger.error(f"배치 이동 중 예외 발생 {source_key}: {e}")
                results[source_key] = False
        
        success_count = sum(1 for success in results.values() if success)
        total_count = len(move_operations)
        
        logger.info(f"배치 이동 완료: {success_count}/{total_count} 성공")
        return results
    
    def get_meta_json(self, main_category: str, sub_category: int, product_id: str) -> dict[str, Any]|None:
        """
        제품의 meta.json 파일을 다운로드하고 파싱합니다.
        
        Args:
            main_category: 메인 카테고리
            sub_category: 서브 카테고리 ID
            product_id: 제품 ID
            
        Returns:
            Optional[Dict[str, Any]]: meta.json 내용 (실패 시 None)
        """
        try:
            meta_key = f"{main_category}/{sub_category}/{product_id}/meta.json"
            
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=meta_key)
            content = response['Body'].read().decode('utf-8')
            
            # JSON 파싱
            meta_data = json.loads(content)
            
            logger.info(f"meta.json 다운로드 성공: {product_id}")
            return meta_data
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                logger.warning(f"meta.json 파일이 없습니다: {product_id}")
            else:
                logger.error(f"meta.json 다운로드 실패 {product_id}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"meta.json JSON 파싱 실패 {product_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"meta.json 처리 중 예상치 못한 오류 {product_id}: {e}")
            return None

    # =============================================================================
    # DynamoDB 관련 함수들
    # =============================================================================
    
    def create_product_item(self, main_category: str, sub_category: int, product_id: str, 
                           file_lists: dict[str, list[str]] = None, recommendation_order: int = 0) -> tuple[bool, bool]:
        """
        DynamoDB에 제품 아이템을 생성하거나 업데이트합니다.
        기존 아이템이 있으면 덮어쓰지 않고 False를 반환하고, 없으면 새로 생성합니다.
        
        Args:
            main_category: 메인 카테고리
            sub_category: 서브 카테고리 ID
            product_id: 제품 ID
            file_lists: 폴더별 파일 리스트 
            recommendation_order: 추천 순서(작을수록 높음)
                example : 
                    {
                        "text" : ["1.jpg", "2.jpg"],
                        "summary" : ["1.jpg", "2.jpg"],
                        "segment" : ["1.jpg", "2.jpg"],
                        "detail" : ["1.jpg", "2.jpg"]
                    }
        Returns:
            tuple[bool, bool]: (처리 성공 여부, 새 제품 여부)
        """
        try:
            # 먼저 기존 아이템 존재 여부 확인
            existing_item = self.get_product_detail(sub_category, product_id)
            is_new_product = existing_item is None
            
            if existing_item:
                logger.info(f"기존 아이템 존재 dynamoDB 데이터 추가하지 않음: {main_category}-{sub_category}-{product_id}")
                return True, False

            current_time = self._get_current_timestamp()
            
            # Format recommendation_order as 7-digit string and combine with product_id
            formatted_order = f"{recommendation_order:07d}#{product_id}"
            sub_category_curation_status = f"{sub_category}#PENDING"

            
            # DynamoDB client용 아이템 구성 (타입 명시 필요)
            item = {
                'main_category': {'S': main_category},
                'sub_category': {'N': str(sub_category)}, # 파티션 키 
                'product_id': {'S': product_id}, # 정렬 키 
                'curation_status': {'S': 'PENDING'}, # GSI 인덱스의 파티션 키 
                'recommendation_order': {'S': formatted_order}, # GSI 인덱스의 정렬 키 (문자열로 변경)
                'caption_status': {'S': 'PENDING'}, # GSI 인덱스의 파티션 키 
                'caption_updated_at': {'S': current_time}, # GSI 인덱스의 정렬 키 
                'created_at': {'S': current_time},
                'sub_category_curation_status': {'S': sub_category_curation_status}
            }
            
            # 파일 리스트 추가 (빈 리스트 포함)
            if file_lists:
                supported_folders = ['detail', 'summary', 'segment', 'text']
                for folder in supported_folders:
                    # 파일명 리스트를 DynamoDB List로 저장 (빈 리스트 허용)
                    string_list = [{'S': filename} for filename in file_lists[folder]]
                    item[folder] = {'L': string_list}
            
            # 제품 아이템 저장
            self.dynamodb_client.put_item(
                TableName=self.table_name,
                Item=item
            )
            
            logger.info(f"DynamoDB 새 제품 추가됨: {main_category}-{sub_category}-{product_id}")
            return True, True
            
        except ClientError as e:
            logger.error(f"DynamoDB 아이템 처리 실패 {main_category}-{sub_category}-{product_id}: {e}")
            return False, False
    
    def get_product_list(self, sub_category: int, limit: int = 50, 
                        exclusive_start_key: dict[str, Any]|None = None) -> tuple[list[dict[str, Any]], dict[str, Any]|None]:
        """
        특정 카테고리의 상품 목록을 페이지 단위로 가져옵니다.
        
        Args:
            sub_category: 서브 카테고리 ID
            limit: 한 번에 가져올 아이템 수
            exclusive_start_key: 페이지네이션을 위한 시작 키
            
        Returns:
            Tuple[List[Dict], Optional[Dict]]: (아이템 리스트, 다음 페이지 키)
        """
        try:
            query_params = {
                'TableName': self.table_name,
                'KeyConditionExpression': 'sub_category = :sub_category',
                'ExpressionAttributeValues': {
                    ':sub_category': {'N': str(sub_category)}
                },
                'Limit': limit,
                'ScanIndexForward': True  # 오름차순 정렬
            }
            
            if exclusive_start_key:
                query_params['ExclusiveStartKey'] = exclusive_start_key
            
            response = self.dynamodb_client.query(**query_params)
            
            # DynamoDB client 응답을 일반 딕셔너리로 변환
            items = []
            for item in response.get('Items', []):
                converted_item = self._convert_dynamodb_item(item)
                items.append(converted_item)
                
            next_key = response.get('LastEvaluatedKey')
            
            logger.info(f"DynamoDB 상품 목록 조회 완료: 카테고리 {sub_category}, 개수: {len(items)}")
            return items, next_key
            
        except ClientError as e:
            logger.error(f"DynamoDB 상품 목록 조회 실패 카테고리 {sub_category}: {e}")
            return [], None
    
    def get_product_by_status(self, status: str ="PENDING", limit: int = 50,
                             exclusive_start_key: dict[str, Any]|None = None, 
                             sub_category: int= 1005) -> tuple[list[dict[str, Any]], dict[str, Any]|None]:
        """
        특정 상태와 서브 카테고리의 상품 목록을 GSI를 통해 조회합니다.
        
        Args:
            status: 큐레이션 상태 ('PENDING', 'COMPLETED', 'PASS', 'IN_PROGRESS')
            limit: 한 번에 가져올 아이템 수
            exclusive_start_key: 페이지네이션을 위한 시작 키
            sub_category: 서브 카테고리 ID (None이면 모든 서브 카테고리)
            
        Returns:
            Tuple[List[Dict], Optional[Dict]]: (아이템 리스트, 다음 페이지 키)
        """

        try:
            query_params = {
                'TableName': self.table_name,
                'IndexName': 'CurationStatus-SubCategory-GSI',
                'KeyConditionExpression': 'sub_category_curation_status = :sub_category_curation_status',
                'ExpressionAttributeValues': {
                    ':sub_category_curation_status': {'S': f"{sub_category}#{status}"}
                },
                'Limit': limit,
                'ScanIndexForward': True # 무신사 추천순서(order)깂이 낮은게 먼저 -> 오름차순 
            }
            
            # # 서브 카테고리 필터 추가
            # if sub_category is not None:
            #     query_params['FilterExpression'] = 'sub_category = :sub_category'
            #     query_params['ExpressionAttributeValues'][':sub_category'] = {'N': str(sub_category)}
            
            if exclusive_start_key:
                query_params['ExclusiveStartKey'] = exclusive_start_key
            
            response = self.dynamodb_client.query(**query_params)
            
            # DynamoDB client 응답을 일반 딕셔너리로 변환
            items = []
            for item in response.get('Items', []):
                converted_item = self._convert_dynamodb_item(item)
                items.append(converted_item)
                
            next_key = response.get('LastEvaluatedKey')
            
            category_info = f", 서브카테고리: {sub_category}" if sub_category is not None else ""
            logger.info(f"DynamoDB 상태별 상품 조회 완료: 상태 {status}{category_info}, 개수: {len(items)}")
            return items, next_key
            
        except ClientError as e:
            logger.error(f"DynamoDB 상태별 상품 조회 실패 상태 {status}, 서브카테고리 {sub_category}: {e}")
            return [], None
    
    def update_curation_result(self, sub_category: int, product_id: str, 
                            representative_images: dict[str, Any],
                            color_variant_images: dict[str, Any],
                            excluded_text_filenames: list[str] = None,
                          completed_by: str|None = None) -> bool:
        """
        DynamoDB에 큐레이션 결과를 업데이트합니다.
        
        Args:
            sub_category: 서브 카테고리 ID
            product_id: 제품 ID
            representative_images: 대표 이미지 딕셔너리 (model_wearing, front_cutout, back_cutout)
            color_variant_images: 색상 변형 이미지 딕셔너리
            excluded_text_filenames: text 필드에서 제외할 파일명 리스트
            completed_by: 작업자 ID
            
        Returns:
            bool: 업데이트 성공 여부
        """
        try:
            
            # 기존 제품 정보 조회 (메인 카테고리 확인용)
            existing_product = self.get_product_detail(sub_category, product_id)
            if not existing_product:
                logger.error(f"제품을 찾을 수 없습니다: {sub_category}-{product_id}")
                return False            
            # SET 표현식 (새로운 값 설정)
            set_expression_parts = ["curation_status = :status" , "sub_category_curation_status = :sub_category_curation_status"]
            expression_values = {
                ':status': {'S': 'COMPLETED'},
                ':sub_category_curation_status': {'S': f"{sub_category}#COMPLETED"}
            }
            
            # 큐레이션 결과 추가 - DynamoDB Map 타입으로 저장
            set_expression_parts.append("representative_assets = :assets")
                
            # 입력받은 데이터를 DynamoDB 저장용 구조로 변환
            dynamodb_assets = {}
                
            # model_wearing -> model
            if 'model_wearing' in representative_images:
                model_info = representative_images["model_wearing"]
                model_folder_name = model_info.get('folder', '')
                model_filename = model_info.get('filename', '')
                if model_folder_name and model_filename:
                    dynamodb_assets['model'] = {'S': f"{model_folder_name}/{model_filename}"}
            
            # front_cutout -> front
            if 'front_cutout' in representative_images:
                front_info = representative_images["front_cutout"]
                front_folder_name = front_info.get('folder', '')
                front_filename = front_info.get('filename', '')
                if front_folder_name and front_filename:
                    dynamodb_assets['front'] = {'S': f"{front_folder_name}/{front_filename}"}
            
            # back_cutout -> back
            if 'back_cutout' in representative_images:
                back_info = representative_images["back_cutout"]
                back_folder_name = back_info.get('folder', '')
                back_filename = back_info.get('filename', '')
                if back_folder_name and back_filename:
                    dynamodb_assets['back'] = {'S': f"{back_folder_name}/{back_filename}"}
            
            # color_variant_images -> color_variant (리스트)
            color_variant_paths = []
            for image_key, color_variant_info in color_variant_images.items():
                folder_name = color_variant_info.get('folder', '')
                filename = color_variant_info.get('filename', '')
                if folder_name and filename:
                    color_variant_paths.append({'S': f"{folder_name}/{filename}"})
            
            if color_variant_paths:
                dynamodb_assets['color_variant'] = {'L': color_variant_paths}
            
            expression_values[':assets'] = {'M': dynamodb_assets}
            
            # 작업자 정보 추가 (completed_by가 제공되지 않으면 현재 AWS 사용자 사용)
            if completed_by is None:
                completed_by = self.get_current_aws_user()
            
            set_expression_parts.append("completed_by = :completed_by")
            expression_values[':completed_by'] = {'S': completed_by}
            
            # REMOVE 표현식 (불필요한 필드 제거)
            remove_expression_parts = []
            expression_attribute_names = {}
            
            # PASS 상태에서 COMPLETED로 변경하는 경우 pass_reason 필드 제거
            if existing_product.get('curation_status') == 'PASS':
                if 'pass_reason' in existing_product:
                    remove_expression_parts.append("pass_reason")
            
            # COMPLETED 상태에서 COMPLETED로 변경하는 경우 큐레이션 관련 필드들 제거
            elif existing_product.get('curation_status') == 'COMPLETED':
                # representative_assets와 completed_by는 새로 설정하므로 제거하지 않음
                pass
            
            # text 필드에서 제외할 파일들 처리
            if excluded_text_filenames:
                text_field = existing_product.get('text', [])
                if text_field:
                    # 제거할 파일들의 인덱스 찾기
                    indices_to_remove = []
                    for i, filename in enumerate(text_field):
                        if filename in excluded_text_filenames:
                            indices_to_remove.append(i)
                    
                    # 역순으로 정렬하여 뒤부터 제거 (인덱스 충돌 방지)
                    indices_to_remove.sort(reverse=True)
                    
                    if indices_to_remove:
                        # ExpressionAttributeNames에 text 필드 추가
                        expression_attribute_names['#text_field'] = 'text'
                        
                        # REMOVE 표현식에 text 인덱스들 추가
                        text_remove_parts = [f"#text_field[{idx}]" for idx in indices_to_remove]
                        remove_expression_parts.extend(text_remove_parts)
                        
                        logger.info(f"text 필드에서 제거할 인덱스: {indices_to_remove}")
                        logger.info(f"제거할 파일명: {[text_field[i] for i in indices_to_remove]}")
                    else:
                        logger.info(f"text 필드에서 제거할 파일을 찾을 수 없습니다: {excluded_text_filenames}")
                else:
                    logger.warning(f"text 필드가 비어있습니다: {sub_category}-{product_id}")

            # 작업 업데이트 시간 추가
            current_time = self._get_current_timestamp()
            set_expression_parts.append("caption_updated_at = :caption_updated_at")
            expression_values[':caption_updated_at'] = {'S': current_time}
            
            # 업데이트 표현식 구성
            update_expression = f"SET {', '.join(set_expression_parts)}"

            
            # REMOVE 표현식이 있는 경우 추가
            if remove_expression_parts:
                update_expression += f" REMOVE {', '.join(remove_expression_parts)}"
            
            
            # 제품 상태 업데이트
            update_params = {
                'TableName': self.table_name,
                'Key': {
                    'sub_category': {'N': str(sub_category)},
                    'product_id': {'S': product_id}
                },
                'UpdateExpression': update_expression,
                'ExpressionAttributeValues': expression_values
            }
            
            # ExpressionAttributeNames가 있는 경우 추가
            if expression_attribute_names:
                update_params['ExpressionAttributeNames'] = expression_attribute_names
            
            self.dynamodb_client.update_item(**update_params)
            
            logger.info(f"DynamoDB 큐레이션 결과 업데이트 성공: {sub_category}-{product_id}")
            if remove_expression_parts:
                logger.info(f"제거된 필드: {remove_expression_parts}")
            return True
            
        except ClientError as e:
            logger.error(f"DynamoDB 큐레이션 결과 업데이트 실패 {sub_category}-{product_id}: {e}")
            return False
    
    def update_product_status_to_pass(self, sub_category: int, product_id: str, pass_reason: str = None) -> bool:
        """
        DynamoDB에서 상품 상태를 PASS로 업데이트합니다.
        이미지 데이터가 이상하거나 보류가 필요한 경우 사용됩니다.
        
        Args:
            sub_category: 서브 카테고리 ID
            product_id: 제품 ID
            pass_reason: 보류 처리 이유 (선택사항)
            
        Returns:
            bool: 업데이트 성공 여부
        """
        try:
            # 기존 제품 정보 조회 (메인 카테고리 확인용)
            existing_product = self.get_product_detail(sub_category, product_id)
            if not existing_product:
                logger.error(f"제품을 찾을 수 없습니다: {sub_category}-{product_id}")
                return False
            
            
            # SET 표현식 (새로운 값 설정)
            set_expression_parts = ["curation_status = :status" , "sub_category_curation_status = :sub_category_curation_status"]
            expression_values = {
                ':status': {'S': 'PASS'},
                ':sub_category_curation_status': {'S': f"{sub_category}#PASS"}
            }
            
            # pass_reason이 제공된 경우 추가
            if pass_reason:
                set_expression_parts.append("pass_reason = :reason")
                expression_values[':reason'] = {'S': pass_reason}
            
            # 작업자 정보 추가 (PASS 처리도 누가 했는지 기록)
            completed_by = self.get_current_aws_user()
            set_expression_parts.append("completed_by = :completed_by")
            expression_values[':completed_by'] = {'S': completed_by}
            
            # REMOVE 표현식 (불필요한 필드 제거)
            remove_expression_parts = []
            
            # COMPLETED 상태에서 COMPLETED로 변경하는 경우 큐레이션 관련 필드들 제거
            if existing_product.get('curation_status') == 'COMPLETED':
                if 'representative_assets' in existing_product:
                    remove_expression_parts.append("representative_assets")
                # completed_by는 PASS 처리에서도 사용하므로 제거하지 않음
            # 작업 업데이트 시간 추가
            current_time = self._get_current_timestamp()
            set_expression_parts.append("caption_updated_at = :caption_updated_at")
            expression_values[':caption_updated_at'] = {'S': current_time}

            # 업데이트 표현식 구성
            update_expression = f"SET {', '.join(set_expression_parts)}"
            
            # REMOVE 표현식이 있는 경우 추가
            if remove_expression_parts:
                update_expression += f" REMOVE {', '.join(remove_expression_parts)}"
            
            # 제품 상태 업데이트
            self.dynamodb_client.update_item(
                TableName=self.table_name,
                Key={
                    'sub_category': {'N': str(sub_category)},
                    'product_id': {'S': product_id}
                },
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values
            )
            
            logger.info(f"DynamoDB 상품 상태 PASS 업데이트 성공: {sub_category}-{product_id}")
            if remove_expression_parts:
                logger.info(f"제거된 필드: {remove_expression_parts}")
            return True
            
        except ClientError as e:
            logger.error(f"DynamoDB 상품 상태 PASS 업데이트 실패 {sub_category}-{product_id}: {e}")
            return False
    
    def update_product_file_lists(self, sub_category: int, product_id: str, 
                                file_lists: dict[str, list[str]]) -> bool:
        """
        DynamoDB에 제품의 파일 리스트를 업데이트합니다.
        
        Args:
            sub_category: 서브 카테고리 ID
            product_id: 제품 ID
            file_lists: 폴더별 파일 리스트 {'detail': ['file1.jpg', 'file2.jpg'], 'summary': [...], ...}
            
        Returns:
            bool: 업데이트 성공 여부
        """
        try:
    
            
            # 업데이트할 필드들 정의
            supported_folders = ['detail', 'summary', 'segment', 'text']
            
            update_expression_parts = []
            expression_values = {}
            
            # 각 폴더별 파일 리스트 추가
            for folder in supported_folders:
                if folder in file_lists:
                    # 파일명 리스트를 DynamoDB List로 저장 (빈 리스트 허용)
                    update_expression_parts.append(f"{folder} = :{folder}")
                    string_list = [{'S': filename} for filename in file_lists[folder]]
                    expression_values[f':{folder}'] = {'L': string_list}
            
            if len(update_expression_parts) == 1:  # timestamp만 있는 경우
                logger.warning(f"업데이트할 파일 리스트가 없습니다: {sub_category}-{product_id}")
                return True
            
            update_expression = "SET " + ", ".join(update_expression_parts)
            
            self.dynamodb_client.update_item(
                TableName=self.table_name,
                Key={
                    'sub_category': {'N': str(sub_category)},
                    'product_id': {'S': product_id}
                },
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values
            )
            
            logger.info(f"DynamoDB 파일 리스트 업데이트 성공: {sub_category}-{product_id}")
            logger.debug(f"업데이트된 파일 리스트: {file_lists}")
            return True
            
        except ClientError as e:
            logger.error(f"DynamoDB 파일 리스트 업데이트 실패 {sub_category}-{product_id}: {e}")
            return False
    
    def get_product_file_lists(self, sub_category: int, product_id: str) -> dict[str, list[str]]:
        """
        DynamoDB에서 제품의 파일 리스트를 조회합니다.
        
        Args:
            sub_category: 서브 카테고리 ID
            product_id: 제품 ID
            
        Returns:
            Dict[str, List[str]]: 폴더별 파일 리스트
        """
        try:
            product_detail = self.get_product_detail(sub_category, product_id)
            
            if product_detail is None:
                logger.warning(f"제품을 찾을 수 없습니다: {sub_category}-{product_id}")
                return {}
            
            file_lists = {}
            supported_folders = ['detail', 'summary', 'segment', 'text']
            
            for folder in supported_folders:
                if folder in product_detail:
                    # DynamoDB에서 List로 저장된 데이터를 리스트로 변환
                    if isinstance(product_detail[folder], list):
                        file_lists[folder] = product_detail[folder]
                    else:
                        file_lists[folder] = []
            
            logger.info(f"파일 리스트 조회 성공: {sub_category}-{product_id}")
            return file_lists
            
        except Exception as e:
            logger.error(f"파일 리스트 조회 실패 {sub_category}-{product_id}: {e}")
            return {}
    
    def get_product_detail(self, sub_category: int, product_id: str) -> dict[str, Any]|None:
        """
        특정 제품의 상세 정보를 조회합니다.
        
        Args:
            sub_category: 서브 카테고리 ID
            product_id: 제품 ID
            
        Returns:
            Optional[Dict]: 제품 정보 (없으면 None)
        """
        try:
            response = self.dynamodb_client.get_item(
                TableName=self.table_name,
                Key={
                    'sub_category': {'N': str(sub_category)},
                    'product_id': {'S': product_id}
                }
            )
            
            item = response.get('Item')
            if item:
                # DynamoDB client 응답을 일반 딕셔너리로 변환
                converted_item = self._convert_dynamodb_item(item)
                logger.info(f"DynamoDB 제품 상세 조회 성공: {sub_category}-{product_id}")
                return converted_item
            else:
                logger.warning(f"DynamoDB 제품 없음: {sub_category}-{product_id}")
                return None
            
        except ClientError as e:
            logger.error(f"DynamoDB 제품 상세 조회 실패 {sub_category}-{product_id}: {e}")
            return None
    
    def delete_all_products_in_category(self, sub_category: int) -> dict[str, Any]:
        """
        특정 서브 카테고리의 모든 제품 데이터를 삭제합니다.
        DynamoDB의 배치 삭제를 사용하여 효율적으로 처리합니다.
        
        Args:
            sub_category: 삭제할 서브 카테고리 ID
            
        Returns:
            Dict[str, Any]: 삭제 결과 {'success': bool, 'deleted_count': int, 'failed_items': list}
        """
        try:
            deleted_count = 0
            failed_items = []

            logger.info(f"카테고리 {sub_category} 전체 데이터 삭제 시작")
        
            # paginator 생성
            paginator = self.dynamodb_client.get_paginator('query')
            
            # paginate 실행 (25개씩 페이지 크기 설정)
            page_iterator = paginator.paginate(
                TableName=self.table_name,
                KeyConditionExpression='sub_category = :sub_category',
                ExpressionAttributeValues={
                    ':sub_category': {'N': str(sub_category)}
                },
                ProjectionExpression='sub_category, product_id',
                PaginationConfig={'PageSize': 25}
            )

            for page in page_iterator:
                items = page.get('Items', [])
                if not items:
                    continue

                delete_requests = []
                for item in items:
                    delete_requests.append({
                        'DeleteRequest': {
                            'Key': {
                                'sub_category': item['sub_category'],
                                'product_id': item['product_id']
                            }
                        }
                    })
                try:
                    batch_response = self.dynamodb_client.batch_write_item(
                        RequestItems={
                            self.table_name: delete_requests
                        }
                    )
                    unprocessed_items = batch_response.get('UnprocessedItems', {})


                    # 처리되지 못한 아이템들 재시도
                    # unprocessed_items = batch_response.get('UnprocessedItems', {})
                    # retry_count = 0
                    # max_retries = 3
                    
                    # while unprocessed_items and retry_count < max_retries:
                    #     retry_count += 1
                    #     logger.warning(f"배치 삭제 재시도 {retry_count}/{max_retries}: {len(unprocessed_items.get(self.table_name, []))}개 아이템")
                        
                    #     # 지수 백오프 적용
                    #     import time
                    #     time.sleep(2 ** retry_count * 0.1)
                        
                    #     retry_response = self.dynamodb_client.batch_write_item(
                    #         RequestItems=unprocessed_items
                    #     )
                    #     unprocessed_items = retry_response.get('UnprocessedItems', {})
                    # 최종적으로 처리되지 못한 아이템들 기록
                    if unprocessed_items:
                        failed_batch = unprocessed_items.get(self.table_name, [])
                        failed_items.extend(failed_batch)
                        logger.error(f"배치 삭제 최종 실패: {len(failed_batch)}개 아이템")
                    
                    # 성공한 아이템 수 계산
                    successful_deletes = len(delete_requests) - len(unprocessed_items.get(self.table_name, []))
                    deleted_count += successful_deletes
                        
                except ClientError as e:
                    logger.error(f"배치 삭제 실패: {e}")
                    failed_items.extend(delete_requests)
            
            logger.info(f"카테고리 {sub_category} 삭제 진행 중: {deleted_count}개 완료")
            success = len(failed_items) == 0
            logger.info(f"카테고리 {sub_category} 삭제 완료: 성공 {deleted_count}개, 실패 {len(failed_items)}개")
            
            return {
                'success': success,
                'deleted_count': deleted_count,
                'failed_items': failed_items,
                'failed_count': len(failed_items)
            }
        
        except ClientError as e:
            logger.error(f"카테고리 {sub_category} 삭제 중 DynamoDB 오류: {e}")
            return {
                'success': False,
                'deleted_count': 0,
                'failed_items': [],
                'failed_count': 0,
                'error': str(e)
            }
        except Exception as e:
            logger.error(f"카테고리 {sub_category} 삭제 중 예상치 못한 오류: {e}")
            return {
                'success': False,
                'deleted_count': 0,
                'failed_items': [],
                'failed_count': 0,
                'error': str(e)
            }
    # =============================================================================
    # 카테고리별 상태 통계 관리 함수들
    # =============================================================================
    
    def get_category_status_stats(self, main_category: str, sub_category: int) -> dict[str, Any] | None:
        """
        특정 카테고리의 상태별 통계를 조회합니다.
        
        Args:
            main_category: 메인 카테고리
            sub_category: 서브 카테고리
            
        Returns:
            Dict: 상태별 통계 정보 또는 None
        """
        try:
            stats_id = f"STATUS_STATS_{main_category}_{sub_category}"
            
            response = self.dynamodb_client.get_item(
                TableName=self.table_name,
                Key={
                    'sub_category': {'N': '0'},
                    'product_id': {'S': stats_id}
                }
            )
            
            item = response.get('Item')
            if item:
                converted_item = self._convert_dynamodb_item(item)
                return converted_item
            else:
                return None
                
        except ClientError as e:
            logger.error(f"카테고리 상태 통계 조회 실패 {main_category}-{sub_category}: {e}")
            return None

    def initialize_category_status_stats(self, main_category: str, sub_category: int) -> bool:
        """
        새 카테고리의 상태별 통계를 초기화합니다. , dynamoDB에 특수 파티션, 정렬키로 지정하여 메인/서브 카테고리에 포함되는 제품에 대한 통계를 관리합니다.
            example : 
            {
                'sub_category': {'N': '0'},
                'product_id': {'S': stats_id},
                'main_category': {'S': main_category},
                'target_sub_category': {'N': str(sub_category)},
                'pending_count': {'N': '0'},
                'completed_count': {'N': '0'},
                'pass_count': {'N': '0'},
                'total_products': {'N': '0'},
            }
        Args:
            main_category: 메인 카테고리
            sub_category: 서브 카테고리
            
        Returns:
            bool: 초기화 성공 여부
        """
        try:
            stats_id = f"STATUS_STATS_{main_category}_{sub_category}"
            
            # # 기존 통계가 있는지 확인
            # existing_stats = self.get_category_status_stats(main_category, sub_category)
            # if existing_stats:
            #     logger.info(f"카테고리 상태 통계가 이미 존재합니다: {main_category}-{sub_category}")
            #     return True
            
            # 초기 통계 데이터 구성 (개별 필드로 저장)
            initial_stats = {
                'sub_category': {'N': '0'},
                'product_id': {'S': stats_id},
                'main_category': {'S': main_category},
                'target_sub_category': {'N': str(sub_category)},
                'pending_count': {'N': '0'},
                'completed_count': {'N': '0'},
                'pass_count': {'N': '0'},
                'total_products': {'N': '0'},
            }
            
            self.dynamodb_client.put_item(
                TableName=self.table_name,
                Item=initial_stats
            )
            
            logger.info(f"카테고리 상태 통계 초기화 성공: {main_category}-{sub_category}")
            return True
            
        except ClientError as e:
            logger.error(f"카테고리 상태 통계 초기화 실패 {main_category}-{sub_category}: {e}")
            return False

    def update_category_status_stats_atomic(self, main_category: str, sub_category: int, 
                                          status_changes: dict[str, int]) -> bool:
        """
        카테고리의 상태별 통계를 원자적(atomic)으로 업데이트합니다. , dynamoDB의 update_item 함수 이용
        DynamoDB의 ADD 연산을 사용하여 동시성 문제를 해결합니다.
        
        Args:
            main_category: 메인 카테고리
            sub_category: 서브 카테고리
            status_changes: 상태별 변화량 {'PENDING': -1, 'COMPLETED': 1}
            
        Returns:
            bool: 업데이트 성공 여부
        """
        try:
            stats_id = f"STATUS_STATS_{main_category}_{sub_category}"
            current_time = self._get_current_timestamp()
            
            # # 통계가 없으면 초기화
            # if not self.get_category_status_stats(main_category, sub_category):
            #     if not self.initialize_category_status_stats(main_category, sub_category):
            #         return False
            
            # 업데이트 표현식 구성
            update_expression_parts = []
            expression_values = {}
            
            total_change = 0
            
            # 상태별 증감 연산 추가
            status_field_mapping = {
                'PENDING': 'pending_count',
                'COMPLETED': 'completed_count', 
                'PASS': 'pass_count'
            }
            
            # field_name : dynamoDB에 저장되는 실제 필드명
            for status, change in status_changes.items():
                if change != 0 and status in status_field_mapping:
                    field_name = status_field_mapping[status]
                    update_expression_parts.append(f"{field_name} = {field_name} + :change_{status.lower()}")
                    expression_values[f':change_{status.lower()}'] = {'N': str(change)}
                    total_change += change
            
            if total_change != 0:
                update_expression_parts.append("total_products = total_products + :total_change")
                expression_values[':total_change'] = {'N': str(total_change)}
            
            if len(update_expression_parts) == 1:  # timestamp만 있는 경우
                logger.debug(f"상태 변화 없음: {main_category}-{sub_category}")
                return True
            
            update_expression = "SET " + ", ".join(update_expression_parts)
            
            # Atomic 업데이트 실행
            self.dynamodb_client.update_item(
                TableName=self.table_name,
                Key={
                    'sub_category': {'N': '0'},
                    'product_id': {'S': stats_id}
                },
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values
            )
            
            logger.info(f"카테고리 상태 통계 업데이트 성공: {main_category}-{sub_category}, 변화: {status_changes}")
            return True
            
        except ClientError as e:
            logger.error(f"카테고리 상태 통계 업데이트 실패 {main_category}-{sub_category}: {e}")
            return False

    def get_category_quick_stats(self, main_category: str, sub_category: int) -> dict[str, int]:
        """
        특정 카테고리의 빠른 상태 통계를 조회합니다.
        GUI에서 카테고리 선택 시 즉시 표시할 용도입니다.
        
        Args:
            main_category: 메인 카테고리
            sub_category: 서브 카테고리
            
        Returns:
            Dict: 상태별 개수 {'pending': 120, 'completed': 25, 'pass': 5, 'total': 150}
        """
        stats = self.get_category_status_stats(main_category, sub_category)
        
        if not stats:
            return {'pending': 0, 'completed': 0, 'pass': 0, 'total': 0}
        
        return {
            'pending': stats.get('pending_count', 0),
            'completed': stats.get('completed_count', 0),
            'pass': stats.get('pass_count', 0),
            'total': stats.get('total_products', 0)
        }

    def get_all_category_status_stats(self) -> dict[str, dict[str, Any]]:
        """
        모든 카테고리의 상태별 통계를 조회합니다.
        
        Returns:
            Dict: 카테고리별 상태 통계 
            example : {
                'TOP_1005': {
                    'pending': 120,
                    'completed': 25,
                    'pass': 5,
                    'total': 150
                },
                'TOP_1006': {
                    'pending': 120,
                    'completed': 25,
                    'pass': 5,
                    'total': 150
                },
            }
        """
        try:
            response = self.dynamodb_client.query(
                TableName=self.table_name,
                KeyConditionExpression='sub_category = :meta_key AND begins_with(product_id, :stats_prefix)',
                ExpressionAttributeValues={
                    ':meta_key': {'N': '0'},
                    ':stats_prefix': {'S': 'STATUS_STATS_'}
                }
            )
            
            stats_dict = {}
            for item in response.get('Items', []):
                converted_item = self._convert_dynamodb_item(item)
                main_category = converted_item.get('main_category')
                target_sub_category = converted_item.get('target_sub_category')
                
                if main_category and target_sub_category:
                    key = f"{main_category}_{target_sub_category}"
                    stats_dict[key] = {
                        'pending': converted_item.get('pending_count', 0),
                        'completed': converted_item.get('completed_count', 0),
                        'pass': converted_item.get('pass_count', 0),
                        'total': converted_item.get('total_products', 0)
                    }
            
            logger.info(f"전체 카테고리 상태 통계 조회 완료: {len(stats_dict)}개 카테고리")
            return stats_dict
            
        except ClientError as e:
            logger.error(f"전체 카테고리 상태 통계 조회 실패: {e}")
            return {}
    
    # =============================================================================
    # 유틸리티 함수들
    # =============================================================================
    
    def get_current_aws_user(self) -> str:
        """
        현재 AWS 사용자 정보를 조회합니다.
        
        Returns:
            str: 사용자 식별자 (IAM 사용자명 또는 ARN)
        """
        try:
            # 현재 호출자 정보 조회
            response = self.sts_client.get_caller_identity()
            
            # IAM 사용자 ARN에서 사용자명 추출
            arn = response.get('Arn', '')
            if 'user/' in arn:
                # IAM 사용자인 경우: arn:aws:iam::123456789012:user/username
                user_name = arn.split('user/')[-1]
                return user_name
            elif 'assumed-role/' in arn:
                # 임시 역할인 경우: arn:aws:sts::123456789012:assumed-role/role-name/session-name
                role_name = arn.split('assumed-role/')[-1].split('/')[0]
                session_name = arn.split('/')[-1]
                return f"{role_name}/{session_name}"
            else:
                # 기타 경우 ARN 전체 반환
                return arn
                
        except ClientError as e:
            logger.error(f"AWS 사용자 정보 조회 실패: {e}")
            return "unknown_user"
        except Exception as e:
            logger.error(f"AWS 사용자 정보 조회 중 예상치 못한 오류: {e}")
            return "unknown_user"
    
    def test_connection(self) -> dict[str, bool]:
        """
        AWS 연결 상태를 테스트합니다.
        
        Returns:
            Dict[str, bool]: 각 서비스별 연결 상태
        """
        results = {}
        
        # S3 연결 테스트
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
            results['s3'] = True
            logger.info("S3 연결 테스트 성공")
        except ClientError:
            results['s3'] = False
            logger.error("S3 연결 테스트 실패")
        
        # DynamoDB 연결 테스트
        try:
            self.dynamodb_client.describe_table(TableName=self.table_name)
            results['dynamodb'] = True
            logger.info("DynamoDB 연결 테스트 성공")
        except ClientError:
            results['dynamodb'] = False
            logger.error("DynamoDB 연결 테스트 실패")
        
        return results
    
    def get_statistics(self) -> dict[str, Any]:
        """
        프로젝트 통계 정보를 조회합니다.
        
        Returns:
            Dict[str, Any]: 통계 정보
        """
        try:
            # DynamoDB 테이블 정보
            table_info = self.dynamodb_client.describe_table(TableName=self.table_name)
            item_count = table_info['Table'].get('ItemCount', 0)
            
            # 상태별 카운트 (GSI 사용)
            status_counts = {}
            for status in ['PENDING', 'COMPLETED']:
                items, _ = self.get_product_by_status(status, limit=1000)  # 대략적인 카운트
                status_counts[status] = len(items)
            
            stats = {
                'total_products': item_count,
                'status_breakdown': status_counts,
            }
            
            logger.info(f"통계 정보 조회 완료: {stats}")
            return stats
            
        except ClientError as e:
            logger.error(f"통계 정보 조회 실패: {e}")
            return {}

    def append_files_to_text_field(self, sub_category: int, product_id: str, 
                                  filenames: list[str]) -> bool:
        """
        DynamoDB의 text 필드에 파일명들을 추가합니다.
        SET과 list_append를 사용하여 기존 리스트에 새로운 파일명들을 추가합니다.
        
        Args:
            sub_category: 서브 카테고리 ID
            product_id: 제품 ID
            filenames: 추가할 파일명 리스트
            
        Returns:
            bool: 업데이트 성공 여부
        """
        try:
            if not filenames:
                logger.info(f"추가할 파일명이 없습니다: {sub_category}-{product_id}")
                return True
            
            # # 기존 제품 정보 조회하여 text 필드 존재 여부 확인
            existing_product = self.get_product_detail(sub_category, product_id)
            if not existing_product:
                logger.error(f"제품을 찾을 수 없습니다: {sub_category}-{product_id}")
                return False
            
            # 새로 추가할 파일명들을 DynamoDB List 형식으로 변환
            new_files_list = [{'S': filename} for filename in filenames]
            
            # 기존 text 필드가 있는지 확인
            has_existing_text = 'text' in existing_product and existing_product['text']
            
            if has_existing_text:
                # 기존 text 필드가 있는 경우: list_append 사용
                update_expression = "SET #text_field = list_append(if_not_exists(#text_field, :empty_list), :new_files)"
                expression_attribute_names = {
                    '#text_field': 'text'
                }
                expression_attribute_values = {
                    ':empty_list': {'L': []},
                    ':new_files': {'L': new_files_list}
                }
                
                logger.info(f"기존 text 필드에 {len(filenames)}개 파일 추가: {filenames}")
            else:
                # text 필드가 없는 경우: 새로 생성
                update_expression = "SET #text_field = :new_files"
                expression_attribute_names = {
                    '#text_field': 'text'
                }
                expression_attribute_values = {
                    ':new_files': {'L': new_files_list}
                }
                
                logger.info(f"새 text 필드 생성하여 {len(filenames)}개 파일 추가: {filenames}")
            
            # DynamoDB 업데이트 실행
            self.dynamodb_client.update_item(
                TableName=self.table_name,
                Key={
                    'sub_category': {'N': str(sub_category)},
                    'product_id': {'S': product_id}
                },
                UpdateExpression=update_expression,
                ExpressionAttributeNames=expression_attribute_names,
                ExpressionAttributeValues=expression_attribute_values
            )
            
            logger.info(f"DynamoDB text 필드 업데이트 성공: {sub_category}-{product_id}")
            logger.debug(f"추가된 파일명: {filenames}")
            return True
            
        except ClientError as e:
            logger.error(f"DynamoDB text 필드 업데이트 실패 {sub_category}-{product_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"DynamoDB text 필드 업데이트 중 예외 발생 {sub_category}-{product_id}: {e}")
            return False

    def remove_files_from_segment_field(self, sub_category: int, product_id: str, 
                                       filenames: list[str]) -> bool:
        """
        DynamoDB의 segment 필드에서 지정된 파일명들을 제거합니다.
        기존 리스트를 가져와서 필터링 후 다시 SET하는 방식을 사용합니다.
        
        Args:
            sub_category: 서브 카테고리 ID
            product_id: 제품 ID
            filenames: 제거할 파일명 리스트
            
        Returns:
            bool: 업데이트 성공 여부
        """
        try:
            if not filenames:
                logger.info(f"제거할 파일명이 없습니다: {sub_category}-{product_id}")
                return True
            
            # 기존 제품 정보 조회하여 segment 필드 확인
            existing_product = self.get_product_detail(sub_category, product_id)
            if not existing_product:
                logger.error(f"제품을 찾을 수 없습니다: {sub_category}-{product_id}")
                return False
            
            # 기존 segment 필드 확인
            existing_segment_files = existing_product.get('segment', [])
            if not existing_segment_files:
                logger.info(f"segment 필드가 비어있습니다: {sub_category}-{product_id}")
                return True
            
            # 제거할 파일명들을 set으로 변환하여 빠른 조회
            filenames_to_remove = set(filenames)
            
            # 기존 파일 리스트에서 제거할 파일들을 필터링
            filtered_files = []
            removed_count = 0
            
            for filename in existing_segment_files:
                if filename not in filenames_to_remove:
                    filtered_files.append(filename)
                else:
                    removed_count += 1
            
            if removed_count == 0:
                logger.info(f"segment 필드에서 제거할 파일이 없습니다: {sub_category}-{product_id}")
                return True
            
            # 필터링된 파일 리스트를 DynamoDB List 형식으로 변환
            filtered_files_list = [{'S': filename} for filename in filtered_files]
            
            # DynamoDB 업데이트 실행
            update_expression = "SET #segment_field = :filtered_files"
            expression_attribute_names = {
                '#segment_field': 'segment'
            }
            expression_attribute_values = {
                ':filtered_files': {'L': filtered_files_list}
            }
            
            self.dynamodb_client.update_item(
                TableName=self.table_name,
                Key={
                    'sub_category': {'N': str(sub_category)},
                    'product_id': {'S': product_id}
                },
                UpdateExpression=update_expression,
                ExpressionAttributeNames=expression_attribute_names,
                ExpressionAttributeValues=expression_attribute_values
            )
            
            logger.info(f"DynamoDB segment 필드 업데이트 성공: {sub_category}-{product_id}")
            logger.info(f"제거된 파일 개수: {removed_count}/{len(filenames)}")
            logger.debug(f"제거된 파일명: {[f for f in filenames if f in filenames_to_remove and f in existing_segment_files]}")
            logger.debug(f"남은 파일 개수: {len(filtered_files)}")
            return True
            
        except ClientError as e:
            logger.error(f"DynamoDB segment 필드 업데이트 실패 {sub_category}-{product_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"DynamoDB segment 필드 업데이트 중 예외 발생 {sub_category}-{product_id}: {e}")
            return False

    def batch_get_product_images_from_data(self, main_category: str, sub_category: int, 
                                          product_data_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        이미 가져온 제품 데이터에서 직접 파일 리스트를 추출하여 이미지 정보를 배치로 수집합니다.
        DynamoDB 추가 호출 없이 pagination으로 가져온 데이터를 활용하여 S3 키를 구성합니다.
        
        Args:
            main_category: 메인 카테고리
            sub_category: 서브 카테고리 ID
            product_data_list: 이미 조회된 제품 데이터 목록 (DynamoDB에서 가져온 데이터)
            
        Returns:
            List[Dict]: 다운로드 작업 목록 [{'product_id': str, 'folder': str, 'filename': str, 'url': str}]
        """
        download_tasks = []
        
        try:
            # 지원하는 폴더명들
            supported_folders = ['detail', 'summary', 'segment', 'text']
            
            for product_data in product_data_list:
                try:
                    product_id = product_data.get('product_id')
                    if not product_id:
                        logger.warning("제품 데이터에 product_id가 없습니다.")
                        continue
                    
                    # 각 폴더별로 파일 리스트 추출
                    for folder in supported_folders:
                        filenames = product_data.get(folder, [])
                        
                        # 빈 리스트인 경우 건너뛰기
                        if not filenames or not isinstance(filenames, list):
                            continue
                        
                        # 각 파일명에 대해 S3 키 구성 및 Presigned URL 생성
                        for filename in filenames:
                            if not filename:  # 빈 문자열 체크
                                continue
                                
                            # S3 키 구성: main_category/sub_category/product_id/folder/filename
                            s3_key = f"{main_category}/{sub_category}/{product_id}/{folder}/{filename}"
                            
                            try:
                                # Presigned URL 생성 (1시간 유효)
                                url = self.s3_client.generate_presigned_url(
                                    'get_object',
                                    Params={'Bucket': self.bucket_name, 'Key': s3_key},
                                    ExpiresIn=3600
                                )
                                
                                download_tasks.append({
                                    'product_id': product_id,
                                    'folder': folder,
                                    'filename': filename,
                                    'url': url,
                                    'key': s3_key
                                })
                                
                            except ClientError as e:
                                logger.warning(f"Presigned URL 생성 실패 {s3_key}: {e}")
                                continue
                    
                    # meta.json 파일도 다운로드 태스크에 추가
                    meta_json_key = f"{main_category}/{sub_category}/{product_id}/meta.json"
                    try:
                        # meta.json Presigned URL 생성 (1시간 유효)
                        meta_url = self.s3_client.generate_presigned_url(
                            'get_object',
                            Params={'Bucket': self.bucket_name, 'Key': meta_json_key},
                            ExpiresIn=3600
                        )
                        
                        download_tasks.append({
                            'product_id': product_id,
                            'folder': 'meta',  # meta.json을 구분하기 위한 특별한 폴더명
                            'filename': 'meta.json',
                            'url': meta_url,
                            'key': meta_json_key
                        })
                        
                    except ClientError as e:
                        logger.debug(f"meta.json Presigned URL 생성 실패 {meta_json_key}: {e}")
                        # meta.json은 필수가 아니므로 경고만 남기고 계속 진행
                    
                    logger.debug(f"제품 {product_id} 이미지 수집 완료 (데이터 기반)")
                    
                except Exception as e:
                    logger.error(f"제품 데이터 처리 실패: {e}")
                    continue
            
            logger.info(f"배치 이미지 수집 완료 (데이터 기반): {len(product_data_list)}개 제품, {len(download_tasks)}개 이미지")
            return download_tasks
            
        except Exception as e:
            logger.error(f"배치 이미지 수집 중 오류 (데이터 기반): {e}")
            return []




# 전역 인스턴스 생성 함수
def create_aws_manager(region_name: str = 'ap-northeast-2', profile_name: str = None) -> AWSManager:
    """
    AWS Manager 인스턴스를 생성합니다.
    
    Args:
        region_name: AWS 리전명
        profile_name: AWS 프로필명
        
    Returns:
        AWSManager: AWS Manager 인스턴스
    """
    return AWSManager(region_name=region_name, profile_name=profile_name)


if __name__ == '__main__':
    # 테스트 코드
    try:
        aws_manager = create_aws_manager()
        
        # 연결 테스트
        connection_results = aws_manager.test_connection()
        print("연결 테스트 결과:", connection_results)
        
        # 통계 조회
        stats = aws_manager.get_statistics()
        print("통계 정보:", stats)
        
    except Exception as e:
        print(f"테스트 실패: {e}") 


        