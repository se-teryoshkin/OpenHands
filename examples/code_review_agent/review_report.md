# Code Review: VacancyService
**Status:** ❌ FAILED

**Summary:** Review completed

**Statistics:**
- 🔴 Errors: 1
- 🟡 Warnings: 1
- 🔵 Info: 0

**Files Reviewed:**
- `/var/folders/15/ndgr3kqs6tl6qwxbf1tt4t5c0000gn/T/code_review_9d9p36ao/src/vacancy_module/test_service.py`
- `/var/folders/15/ndgr3kqs6tl6qwxbf1tt4t5c0000gn/T/code_review_9d9p36ao/src/vacancy_module/service.py`

## Issues

### Signature Mismatch

🔴 **[ERROR]** Method `get_vacancy` return type does not match specification. Expected `VacancyResponse` but implementation returns `ExternalVacancyResponse`.
   - File: `/var/folders/15/ndgr3kqs6tl6qwxbf1tt4t5c0000gn/T/code_review_9d9p36ao/src/vacancy_module/service.py`
   - Line: 71
   - Spec: Spec M4: get_vacancy should return VacancyResponse
   - 💡 Suggestion: Import the correct `VacancyResponse` model and adjust the return type and conversion logic to produce a `VacancyResponse` instance.

### Test Quality

🟡 **[WARNING]** Tests only verify method existence using `hasattr` and do not invoke any service methods to validate behavior.
   - File: `/var/folders/15/ndgr3kqs6tl6qwxbf1tt4t5c0000gn/T/code_review_9d9p36ao/src/vacancy_module/test_service.py`
   - Line: 12
   - Spec: Tests should verify functional behavior, not just interface presence.
   - 💡 Suggestion: Add tests that call `create_vacancy`, `get_vacancy`, and `list_vacancies` with mocked HHClientImplementation to assert correct interactions and returned data.
