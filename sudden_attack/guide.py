"""프로그램이 대신 못 해주는 것들 — 그리고 일부러 안 하는 것들.

게임 안 옵션과 그래픽카드 제어판은 회사마다 화면이 다르고 버전마다 바뀐다.
잘못 짚으면 엉뚱한 값을 건드리게 되므로 자동으로 바꾸지 않는다. 대신 무엇을 어떻게
두면 되는지 적어서 보여준다.

맨 아래 '일부러 안 건드리는 것' 은 빼먹은 게 아니라 뺀 것이다. 그 이유까지 적어둔다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Section:
    title: str
    lead: str = ""
    items: list = field(default_factory=list)     # (무엇, 어떻게) 짝


IN_GAME = Section(
    title="게임 안에서 (직접)",
    lead="서든어택을 켜고 옵션에서 맞추시면 됩니다. 여기가 사실 제일 크게 바뀝니다.",
    items=[
        ("화면 모드", "전체 화면. 창 모드나 테두리 없는 창은 윈도우를 한 번 더 "
                   "거쳐 나가서 입력이 늦게 느껴집니다."),
        ("해상도", "모니터 원래 해상도. 낮추면 프레임은 오르지만 화면이 늘어나 보이고 "
                "적이 작아집니다."),
        ("수직 동기(V-Sync)", "끄기. 프레임을 모니터에 맞춰 붙잡아 두는 기능이라 "
                          "그만큼 입력이 늦어집니다."),
        ("그림자 · 효과 · 안티앨리어싱", "낮음 또는 끄기. 프레임도 오르지만 적을 가리는 "
                                "화면 요소가 줄어드는 이득이 더 큽니다."),
        ("프레임 제한", "모니터 주사율보다 넉넉히 높게, 또는 해제."),
        ("마우스 감도", "게임 안에서만 조절하세요. 윈도우 포인터 속도는 가운데(6단)에 "
                    "두는 것이 1:1 로 전달됩니다."),
    ],
)

NVIDIA = Section(
    title="NVIDIA 제어판 (직접)",
    lead="바탕화면 우클릭 → NVIDIA 제어판 → 3D 설정 관리 → 프로그램 설정에서 서든어택 선택.",
    items=[
        ("전원 관리 모드", "최고 성능 선호"),
        ("저지연 모드", "켬 (가능하면 '최고')"),
        ("수직 동기", "끔"),
        ("텍스처 필터링 - 품질", "고성능"),
        ("셰이더 캐시 크기", "10GB 이상 또는 무제한 — 맵을 처음 볼 때 튀는 렉이 줄어듭니다"),
    ],
)

AMD = Section(
    title="AMD 소프트웨어 (직접)",
    lead="AMD Software → 게임 → 서든어택 선택 (또는 그래픽 → 게임).",
    items=[
        ("Radeon Anti-Lag", "사용"),
        ("수직 동기 대기", "항상 끄기"),
        ("텍스처 필터링 품질", "성능"),
        ("표면 형식 최적화", "사용"),
        ("Radeon Boost / Chill", "끄기 — 프레임을 일부러 낮추는 기능입니다"),
    ],
)

INTEL = Section(
    title="Intel 그래픽 설정 (직접)",
    lead="Intel Graphics Command Center → 게임.",
    items=[
        ("Adaptive Sync / 수직 동기", "끄기"),
        ("이미지 선명화", "끄기"),
        ("전원 → 그래픽 성능 기본 설정", "최대 성능"),
    ],
)

MONITOR = Section(
    title="모니터 본체 (직접)",
    lead="모니터 아래 버튼으로 들어가는 설정입니다. 여기가 막혀 있으면 윈도우에서 "
         "주사율을 올려도 최대가 안 나옵니다.",
    items=[
        ("주사율(Refresh Rate)", "게이밍 모니터는 최대 주사율을 모니터 메뉴에서 따로 켜야 "
                             "하는 경우가 많습니다. 한 번 확인해 보세요."),
        ("오버드라이브 / 응답속도", "중간 단계. 제일 높은 단계는 오히려 잔상이 생깁니다."),
        ("케이블", "144Hz 이상이면 DisplayPort 를 쓰세요. 오래된 HDMI 는 주사율이 막힙니다."),
    ],
)

AVOIDED = Section(
    title="일부러 안 건드리는 것",
    lead="다른 '최적화 프로그램' 들이 흔히 손대는 것들입니다. 빠뜨린 게 아니라, "
         "얻는 것보다 잃는 게 커서 뺐습니다.",
    items=[
        ("bcdedit — HPET · 플랫폼 클럭", "잘못 되면 윈도우가 아예 안 켜집니다. "
                                   "프레임이 오른다는 근거도 확실하지 않습니다."),
        ("가상 메모리(페이지 파일) 끄기", "메모리가 모자라는 순간 게임이 경고 없이 꺼집니다."),
        ("윈도우 서비스 대량 정지", "소리가 안 나거나 업데이트가 멈추는 식으로 "
                            "한참 뒤에 조용히 고장 납니다."),
        ("레지스트리 청소 · 임시파일 삭제", "프레임과 관계가 없습니다. "
                                 "지운 것을 되돌릴 수도 없습니다."),
        ("게임 파일 자체 수정", "게임을 바꾸는 일이라 계정이 막힐 수 있습니다. "
                        "이 프로그램은 윈도우 설정만 건드립니다."),
    ],
)


def sections(spec=None) -> list[Section]:
    """이 컴퓨터에 맞는 안내만 골라준다."""
    chosen = [IN_GAME]
    names = " ".join(getattr(spec, "gpus", []) or []).lower()
    picked = False
    for needle, section in (
        (("nvidia", "geforce", "rtx ", "gtx "), NVIDIA),
        (("radeon", "amd "), AMD),
        (("intel", "arc ", "iris"), INTEL),
    ):
        if any(word in names for word in needle):
            chosen.append(section)
            picked = True
    if not picked:
        # 그래픽카드를 못 읽었으면 셋 다 보여준다. 골라 읽으시면 된다.
        chosen.extend([NVIDIA, AMD, INTEL])
    chosen.append(MONITOR)
    chosen.append(AVOIDED)
    return chosen
