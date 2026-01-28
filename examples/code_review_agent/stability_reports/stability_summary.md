# Stability Test Summary

**Test Configuration:**
- Specification: `/Users/ngc436/Documents/projects/OpenHands/test_data/module_M5/M5.md`
- Code: `/Users/ngc436/Documents/projects/OpenHands/test_data/module_M5/M5_run1_before_CR_1.zip`
- Number of runs: 3

## Execution Statistics

- Average time: 76.49s
- Min time: 67.50s
- Max time: 81.20s
- Time std dev: 6.36s

## Issue Consistency Analysis

- Total unique issues found: 24
- Consistent issues (appeared in all 3 runs): 6
- Inconsistent issues: 18

### Consistent Issues (100% occurrence)

- **code_quality_issue** in `.downloads/example_usage.py` (line 55): 'except Exception:' without re-raise suppresses all errors. Either handle specific exceptions or re-
- **cross_file_issue** in `src/storage_module/service.py` (line 145): Possibly invalid member access 'self.redis_client' (could not resolve 'redis_client' on inferred pro
- **cross_file_issue** in `src/storage_module/service.py` (line 189): Possibly invalid member access 'ChatResponse.model_validate_json' (could not resolve 'model_validate
- **cross_file_issue** in `src/storage_module/service.py` (line 315): Possibly invalid member access 'SearchDetailsResponse.model_validate_json' (could not resolve 'model
- **cross_file_issue** in `src/storage_module/service.py` (line 373): Possibly invalid member access 'ScreeningTaskInfo.model_validate_json' (could not resolve 'model_val
- **cross_file_issue** in `src/storage_module/service.py` (line 386): Possibly invalid member access 'ScreeningTaskInfo.model_validate_json' (could not resolve 'model_val

### Inconsistent Issues

| Category | File | Line | Message | Occurrences |
|----------|------|------|---------|-------------|
| signature_mismatch | `unknown` | N/A | Method 'start_screening' from spec not implemented... | 2/3 |
| signature_mismatch | `unknown` | N/A | Method 'get_screening_status' from spec not implemented... | 2/3 |
| signature_mismatch | `unknown` | N/A | Method 'wait_for_screening' from spec not implemented... | 2/3 |
| test_quality | `unknown` | N/A | Tests only check method existence with hasattr() (4 times) b... | 2/3 |
| pydantic_issue | `src/storage_module/models.py` | N/A | Model 'IdNameObject' is defined in multiple files: src/stora... | 2/3 |
| test_quality | `tests/vacancy_module/test_functional_works.py` | N/A | Tests only check method existence with hasattr() (5 times) b... | 2/3 |
| test_quality | `tests/chat_module/test_chat_service_comprehensive.py` | N/A | Tests only check method existence with hasattr() (6 times) b... | 2/3 |
| test_quality | `tests/supervisor_module/test_tool_integration.py` | N/A | Tests only check method existence with hasattr() (4 times) b... | 2/3 |
| test_quality | `tests/screening_module/test_screening_service.py` | N/A | Tests only check method existence with hasattr() (2 times) b... | 2/3 |
| test_quality | `tests/supervisor_module/test_singleton_pattern.py` | N/A | Tests only check method existence with hasattr() (4 times) b... | 2/3 |
| test_quality | `tests/supervisor_module/test_supervisor_service.py` | N/A | Tests only check method existence with hasattr() (7 times) b... | 2/3 |
| code_quality_issue | `src/supervisor_module/service.py` | 245 | 'except Exception:' without re-raise suppresses all errors. ... | 1/3 |
| code_quality_issue | `src/supervisor_module/service.py` | 166 | 'except Exception:' without re-raise suppresses all errors. ... | 1/3 |
| test_quality | `unknown` | N/A | Tests only check method existence with hasattr() (7 times) b... | 1/3 |
| test_quality | `unknown` | N/A | Tests only check method existence with hasattr() (5 times) b... | 1/3 |
| test_quality | `unknown` | N/A | Tests only check method existence with hasattr() (2 times) b... | 1/3 |
| test_quality | `unknown` | N/A | Tests only check method existence with hasattr() (6 times) b... | 1/3 |
| pydantic_issue | `src/vacancy_module/service.py` | N/A | Model 'IdNameObject' is defined in multiple files: src/stora... | 1/3 |

## Individual Run Results

### Run 1

- Status: ❌ FAILED
- Errors: 3
- Warnings: 15
- Time: 80.77s
- Report: `review_report_1.md`

### Run 2

- Status: ❌ FAILED
- Errors: 3
- Warnings: 13
- Time: 81.20s
- Report: `review_report_2.md`

### Run 3

- Status: ❌ FAILED
- Errors: 0
- Warnings: 13
- Time: 67.50s
- Report: `review_report_3.md`

