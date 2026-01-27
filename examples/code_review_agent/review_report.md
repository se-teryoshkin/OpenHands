# Code Review: VacancyService
**Status:** ❌ FAILED

**Summary:** Review completed

**Statistics:**
- 🔴 Errors: 1
- 🟡 Warnings: 1
- 🔵 Info: 0

**Files Reviewed:**
- `/var/folders/15/ndgr3kqs6tl6qwxbf1tt4t5c0000gn/T/code_review_2mtqxudv/src/vacancy_module/test_service.py`
- `/var/folders/15/ndgr3kqs6tl6qwxbf1tt4t5c0000gn/T/code_review_2mtqxudv/src/vacancy_module/service.py`

## Issues

### Signature Mismatch

🔴 **[ERROR]** Method `get_vacancy` return type does not match specification. Expected `VacancyResponse` but implementation returns `ExternalVacancyResponse`.
   - File: `/var/folders/15/ndgr3kqs6tl6qwxbf1tt4t5c0000gn/T/code_review_2mtqxudv/src/vacancy_module/service.py`
   - Line: 71
   - Spec: M4.md: get_vacancy return type VacancyResponse
   - 💡 Suggestion: Import and use the `VacancyResponse` model defined in the specification, and map all required fields accordingly.

### Test Quality

🟡 **[WARNING]** Tests only verify method existence using `hasattr` and do not invoke any service methods to validate behavior.
   - File: `/var/folders/15/ndgr3kqs6tl6qwxbf1tt4t5c0000gn/T/code_review_2mtqxudv/src/vacancy_module/test_service.py`
   - Line: 12
   - Spec: M4.md: test coverage
   - 💡 Suggestion: Add tests that call `create_vacancy`, `get_vacancy`, and `list_vacancies` with mocked HHClientImplementation to assert correct interactions and returned data.
