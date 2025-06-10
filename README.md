# 프로젝트: 감정 분석 및 이상 탐지

이 저장소에는 댓글 데이터의 전처리, 분석, 모델 학습을 위한 Jupyter 노트북이 포함되어 있습니다. 데이터 파일은 용량이 크므로 아래 Google Drive 링크에서 다운로드하여 사용하세요.

---

## 📁 디렉토리 구조

```
/                # 프로젝트 루트
├─ notebooks/    # Jupyter 노트북
│   ├─ emotion_anomaly_detection.ipynb   # 시계열 감정 이상 탐지
│   ├─ data_preprocessing_analysis.ipynb # 데이터 전처리 및 탐색적 분석
│   └─ train_kcelectra_kote.ipynb        # KcELECTRA-KOTE 모델 학습
└─ README.md     # 이 파일
```

---

## 📝 노트북 설명

- **emotion_anomaly_detection.ipynb**
  주간 감정 비율을 기준으로 Z-스코어와 Isolation Forest를 이용해 이상치를 탐지합니다.

- **data_preprocessing_analysis.ipynb**
  원본 기사·댓글 데이터를 불러와 날짜 파싱, 형태소 분석(Okt), 불용어 제거 후 단어 빈도 및 월별 트렌드를 시각화합니다.

- **train_kcelectra_kote.ipynb**
  KcELECTRA 모델을 KOTE 감정 분류용으로 파인튜닝하고, 학습 및 평가 과정을 담고 있습니다.

---

## 📂 데이터

데이터 파일은 Google Drive에 업로드되어 있습니다. 다운로드 후 프로젝트 루트에 `data/` 폴더를 만들어 넣어주세요.
이후 파일 경로에 대한 설정 또한 필요합니다.

[데이터 다운로드](https://drive.google.com/drive/folders/1tInRKcn0RbFu9oZyvDHfzTM2vRJT7FZq?usp=sharing)

---
**사용한 데이터셋에 대해서...**

[Korean Online That-gul Emotions](https://github.com/searle-j/KOTE)

12개의 도메인에 대하여, 50,000건의 댓글 데이터에 대해 한 건당 최소 다섯 명 이상의 인원이 감정을 라벨링

---

## ⚙️ 실행 환경 및 주요 라이브러리

- Python 3.10 이상
- pandas, matplotlib, wordcloud, konlpy, scikit-learn, torch, transformers

```bash
pip install pandas matplotlib wordcloud konlpy scikit-learn torch transformers...
```

---
