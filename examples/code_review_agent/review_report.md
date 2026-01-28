# Code Review: VacancyService
**Status:** ❌ FAILED

**Summary:** Two issues were detected: a signature mismatch for `get_vacancy` and insufficient test coverage that only verifies method existence.

**Statistics:**
- 🔴 Errors: 1
- 🟡 Warnings: 1
- 🔵 Info: 0

**Files Reviewed:**
- `/var/folders/15/ndgr3kqs6tl6qwxbf1tt4t5c0000gn/T/code_review_7snp3cjg/src/vacancy_module/service.py`
- `/var/folders/15/ndgr3kqs6tl6qwxbf1tt4t5c0000gn/T/code_review_7snp3cjg/src/vacancy_module/test_service.py`
- `/var/folders/15/ndgr3kqs6tl6qwxbf1tt4t5c0000gn/T/code_review_7snp3cjg/src/vacancy_module/__init__.py`

## Issues

### Signature Mismatch

🔴 **[ERROR]** Return type mismatch in 'get_vacancy': expected 'VacancyResponse', got ''ExternalVacancyResponse''
   - File: `signatures_service.py`
   - 💡 Suggestion: Update the `get_vacancy` method signature to return `VacancyResponse` as defined in the specification, or rename/convert `ExternalVacancyResponse` to match `VacancyResponse`. Ensure the returned object conforms to the expected schema.

### Test Quality

🟡 **[WARNING]** Tests only check method existence with hasattr() (3 times) but never actually call the methods. Tests should invoke methods and verify behavior.
   - File: `test_quality_test_service.py`
   - 💡 Suggestion: Enhance the test suite to call `create_vacancy`, `get_vacancy`, and `list_vacancies` on a properly mocked `HHClientImplementation`. Assert expected return values, side‑effects, and error handling instead of merely checking for method presence.
