from pr_agent.report import render_alternatives

LOG = [
    {
        "current": {"head_total": "398.29", "changed_total": "398.29", "delta": "0.00"},
        "alternatives": [
            {
                "changes": [
                    {
                        "logical_id": "Web",
                        "action": "set",
                        "property": "InstanceType",
                        "value": "m7g.large",
                    }
                ],
                "result": {"head_total": "398.29", "changed_total": "167.46", "delta": "-230.83"},
            },
            {
                "changes": [{"logical_id": "NatC", "action": "remove"}],
                "result": {"head_total": "398.29", "changed_total": "353.03", "delta": "-45.26"},
            },
        ],
    }
]


def test_render_alternatives_shows_changes_totals_and_deltas_from_tool_results() -> None:
    # Act
    markdown = render_alternatives(LOG)

    # Assert
    assert "| Current (head) | - | 398.29 | 0.00 |" in markdown
    assert "| 1 | Web: InstanceType → m7g.large | 167.46 | -230.83 |" in markdown
    assert "| 2 | NatC: remove | 353.03 | -45.26 |" in markdown


def test_render_alternatives_marks_errors() -> None:
    # Arrange
    log = [
        {
            "current": {"changed_total": "398.29"},
            "alternatives": [
                {"changes": [{"logical_id": "Web", "action": "remove"}], "result": {"error": "no"}}
            ],
        }
    ]

    # Act
    markdown = render_alternatives(log)

    # Assert
    assert "| 1 | Web: remove | could not be estimated | - |" in markdown


def test_render_alternatives_redacts_unsafe_names() -> None:
    # Arrange
    change = {"logical_id": "Web", "action": "set", "property": "InstanceType", "value": "x y"}
    log = [
        {
            "current": {"changed_total": "398.29"},
            "alternatives": [{"changes": [change], "result": {"error": "invalid"}}],
        }
    ]

    # Act
    markdown = render_alternatives(log)

    # Assert
    assert "Web: InstanceType → [redacted]" in markdown


def test_render_alternatives_redacts_urls_in_values() -> None:
    # Arrange
    change = {
        "logical_id": "Web",
        "action": "set",
        "property": "InstanceType",
        "value": "https://evil.example/x",
    }
    log = [
        {
            "current": {"changed_total": "398.29"},
            "alternatives": [{"changes": [change], "result": {"error": "invalid"}}],
        }
    ]

    # Act
    markdown = render_alternatives(log)

    # Assert
    assert "evil.example" not in markdown


def test_render_alternatives_signs_increases_and_shows_memory_headroom() -> None:
    results_log = [
        {
            "current": {
                "changed_total": "0.0161",
                "delta": "0.0000",
                "memory": [
                    {
                        "logical_id": "Aggregate",
                        "memory_mb": 1536,
                        "peak_mb": "1187",
                        "headroom_percent": 23,
                    }
                ],
            },
            "alternatives": [
                {
                    "changes": [
                        {
                            "logical_id": "Aggregate",
                            "action": "set",
                            "property": "MemorySize",
                            "value": 2048,
                        }
                    ],
                    "result": {
                        "changed_total": "0.0178",
                        "delta": "0.0017",
                        "memory": [
                            {
                                "logical_id": "Aggregate",
                                "memory_mb": 2048,
                                "peak_mb": "1192",
                                "headroom_percent": 42,
                            }
                        ],
                    },
                }
            ],
        }
    ]

    table = render_alternatives(results_log).splitlines()

    assert table[2] == "| # | Changes | Monthly | Delta vs. this PR | Memory (peak / set) |"
    assert (
        table[4] == "| Current (head) | - | 0.0161 | 0.0000 | Aggregate: 1187 / 1536 MB, 23% free |"
    )
    assert table[5] == (
        "| 1 | Aggregate: MemorySize → 2048 | 0.0178 | +0.0017"
        " | Aggregate: 1192 / 2048 MB, 42% free |"
    )


def test_render_alternatives_shows_requirement_checks_with_limits() -> None:
    def checks(cost: bool, memory: bool | None) -> dict[str, object]:
        return {
            "cost": {"ok": cost, "limit": "1.00", "requirement": "R-05", "not_priced": 2},
            "memory": {"ok": memory, "limit": "10", "requirement": "R-04"},
        }

    log = [
        {
            "current": {
                "changed_total": "0.0215",
                "delta": "0.0000",
                "checks": checks(True, False),
            },
            "alternatives": [
                {
                    "changes": [
                        {
                            "logical_id": "Fn",
                            "action": "set",
                            "property": "MemorySize",
                            "value": 2048,
                        }
                    ],
                    "result": {
                        "changed_total": "0.0230",
                        "delta": "0.0015",
                        "checks": checks(True, None),
                    },
                }
            ],
        }
    ]

    table = render_alternatives(log).splitlines()

    assert table[2] == "| # | Changes | Monthly | Delta vs. this PR | Meets requirement limits |"
    assert table[4] == (
        "| Current (head) | - | 0.0215 | 0.0000 | R-05 cost under 1.00 USD: yes"
        " (2 resources not priced); R-04 memory free at least 10%: no |"
    )
    assert table[5].endswith("R-04 memory free at least 10%: unknown |")


def test_render_alternatives_notes_that_measurements_come_from_the_pull_request() -> None:
    log = [
        {
            "current": {
                "changed_total": "0.0215",
                "delta": "0.0000",
                "memory": [
                    {
                        "logical_id": "Fn",
                        "memory_mb": 1536,
                        "peak_mb": "1523",
                        "headroom_percent": 1,
                    }
                ],
            },
            "alternatives": [],
        }
    ]

    table = render_alternatives(log)

    assert table.endswith(
        "\nMeasured durations and peak memory come from usage.json in this pull request.\n"
    )
