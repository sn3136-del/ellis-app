// Realistic demo template cases seeded on first launch. Fictional people and
// companies; document text mirrors real USCIS / IRCC artifacts so every Ellis
// capability produces convincing output during a live demo.

function ts(daysAgo) { return Date.now() - daysAgo * 86400000 }
function doc(name, text) { return { id: 'doc_' + Math.random().toString(36).slice(2, 9), name, text: text.trim(), addedAt: ts(3), extracted: null } }
function task(title, owner, due, priority, status = 'open') { return { id: 'task_' + Math.random().toString(36).slice(2, 9), title, owner, due, priority, status, why: '' } }

export function seedCases() {
  return [
    // ---------------- USA · WORK · Vietnam -> USA · H-1B · deep24 ----------------
    {
      id: 'seed_us_work_vn', createdAt: ts(47), updatedAt: ts(1),
      applicantName: 'Bao Tran', originCountry: 'Vietnam', destinationCountry: 'USA',
      pathway: 'work', visaType: 'H-1B', employer: 'deep24, Inc.', ownerRole: 'employer',
      facts: {
        stage: 'Filing', 'Passport No.': 'C8214563', 'Date of Birth': '1996-07-12',
        Nationality: 'Vietnam', Sex: 'M', 'Passport Expiry': '2033-03-04',
        'Job Title': 'Founding Engineer', Position: 'Founding Engineer',
        'Offered Wage': 'USD 168,000 / year', 'Annual Salary': 'USD 168,000',
        'Equity': '1.25% (48-month vest, 12-month cliff)',
        'Current Status': 'F-1 STEM OPT', 'SEVIS / EAD': 'EAD (c)(3)(B) valid to 2027-01-14',
        'Status Valid Until': '2027-01-14', 'I-94': '882314905A2',
        'U.S. Address': '1160 Battery St East, Apt 407, San Francisco, CA 94111',
        'Worksite': 'San Francisco, CA', 'Employer Address': '535 Mission St, 14th Floor, San Francisco, CA 94105',
        'FEIN': '88-3921647', 'Degree': 'M.S. Computer Science, University of Washington',
        'Receipt Number': 'WAC2690038214', 'Premium Processing': 'Yes (Form I-907, 15-day)',
        'Start Date': '2026-10-01', 'Dependents': 'None'
      },
      documents: [
        doc('Passport — Vietnam', `SOCIALIST REPUBLIC OF VIETNAM — PASSPORT / HO CHIEU
Type: P   Country Code: VNM
Passport No.: C8214563
Surname: TRAN
Given Names: BAO
Nationality: VIETNAMESE
Date of Birth: 12 JUL 1996
Sex: M
Place of Birth: HO CHI MINH CITY
Date of Issue: 05 MAR 2023
Date of Expiry: 04 MAR 2033
Authority: IMMIGRATION DEPARTMENT
P<VNMTRAN<<BAO<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
C82145636VNM9607121M3303044<<<<<<<<<<<<<<02`),
        doc('I-797C Receipt Notice (I-129)', `U.S. CITIZENSHIP AND IMMIGRATION SERVICES
Form I-797C, Notice of Action
Receipt Number: WAC2690038214
Notice Type: Receipt Notice
Case Type: I-129, Petition for a Nonimmigrant Worker
Classification: H-1B — Specialty Occupation
Petitioner: DEEP24, INC.
Beneficiary: TRAN, BAO
Received Date: 2026-04-02
Notice Date: 2026-04-05
Requested Validity: 2026-10-01 to 2029-09-30
Service Center: California Service Center
Premium Processing: Requested (Form I-907) — clock started 2026-04-02
Cap: FY2027 regular cap — registration selected 2026-03-14 (Beneficiary ID VN26-118834)`),
        doc('Labor Condition Application (LCA)', `LABOR CONDITION APPLICATION (ETA FORM 9035E)
Case Number: I-200-26088-284531
Case Status: CERTIFIED
Certification Date: 2026-03-27   Period: 2026-10-01 to 2029-09-30
Employer: DEEP24, INC.  (FEIN 88-3921647)
Job Title: Founding Engineer (Software Developer)
SOC Code: 15-1252 — Software Developers
Wage Level: Level II
Prevailing Wage: USD 149,781 per year (OEWS, San Francisco-Oakland-Berkeley MSA)
Offered Wage: USD 168,000 per year
Worksite: 535 Mission St, 14th Floor, San Francisco, CA 94105
Full-Time: Yes   H-1B Dependent: No   Willful Violator: No
Public Disclosure: Notice posted at worksite 2026-03-15 to 2026-03-25`),
        doc('Employment Agreement — Founding Engineer', `DEEP24, INC.
A Delaware Corporation — 535 Mission St, 14th Floor, San Francisco, CA 94105
Date: 2026-02-20
Re: Employment Agreement — Founding Engineer
Employee: Bao Tran
Position: Founding Engineer, reporting to the Chief Executive Officer
Base Salary: USD 168,000 per year, paid semi-monthly, full-time exempt
Equity: 1,250,000 options (1.25% fully diluted), 48-month vesting, 12-month cliff, per the 2025 Stock Plan
Start Date: 2026-10-01 (contingent on H-1B approval and work authorization)
Duties: Architect and build deep24's core inference platform; own model-serving
infrastructure (Kubernetes, CUDA, distributed training); lead hiring for the founding
engineering team. Minimum qualification: Master's degree in Computer Science or a
closely related engineering field, or equivalent.
At-will employment. Governed by the laws of the State of California.
/s/ Minh Le, Chief Executive Officer`),
        doc('Board Resolution — Right to Control', `DEEP24, INC. — UNANIMOUS WRITTEN CONSENT OF THE BOARD OF DIRECTORS
Date: 2026-03-10
RESOLVED, that the employment of Bao Tran as Founding Engineer is subject to the
oversight, direction, and control of the Company acting through its Chief Executive
Officer and Board of Directors, including the authority to hire, pay, supervise,
discipline, and terminate;
RESOLVED FURTHER, that Mr. Tran's equity interest of 1.25% is non-controlling and
confers no board seat or veto rights;
RESOLVED FURTHER, that the Company is authorized to file a Form I-129 H-1B petition
on behalf of Mr. Tran and to maintain the associated Public Access File.
Directors: Minh Le (CEO), Sarah Kim (COO), David Okafor (Independent)`),
        doc('Resume / CV', `BAO TRAN — FOUNDING ENGINEER
EDUCATION
- M.S. Computer Science, University of Washington, 2023 (GPA 3.92/4.0)
- B.Eng. Computer Science, Hanoi University of Science and Technology, 2018
EXPERIENCE
- Founding Engineer, deep24, Inc. (F-1 STEM OPT), 2024-present — built the model-serving
  platform from zero to 40M daily inferences; first engineering hire
- Software Engineer, VNG Corporation (Ho Chi Minh City), 2018-2021 — payments infrastructure
- Graduate Research Assistant, UW Systems Lab, 2021-2023 — distributed ML scheduling
PUBLICATIONS: 2 peer-reviewed (OSDI workshop, MLSys). Kubernetes, CUDA, Go, Rust, Python.`),
        doc('Degree + Credential Evaluation (WES)', `EDUCATIONAL CREDENTIAL EVALUATION
Candidate: Bao Tran
Foreign Credential: Bachelor of Engineering in Computer Science,
Hanoi University of Science and Technology, Vietnam (2018)
U.S. Equivalency: Bachelor of Science in Computer Science
U.S. Graduate Degree: M.S. Computer Science, University of Washington (2023) — verified via National Student Clearinghouse
Evaluator: World Education Services (WES) — Reference 26-583912
Evaluation Type: Course-by-Course, ICAP`),
        doc('I-94 + STEM OPT EAD', `U.S. CBP — I-94 ADMISSION RECORD
Name: TRAN, BAO
Admission (I-94) Number: 882314905A2
Class of Admission: F-1 (D/S)
Most Recent Entry: 2023-09-02 (San Francisco, SFO)
SEVIS ID: N0042817765
EMPLOYMENT AUTHORIZATION DOCUMENT (Form I-766)
Category: (c)(3)(B) — STEM OPT Extension
Valid From: 2025-01-15   Card Expires: 2027-01-14
Employer on Form I-983: DEEP24, INC. (E-Verify Company ID 1748291)
Note: Cap-gap not required — EAD outlasts the 2026-10-01 requested H-1B start date.`)
      ],
      tasks: [
        task('Register for FY2027 H-1B cap lottery', 'Employer', 'Selected 2026-03-14', 'high', 'done'),
        task('Certify LCA (ETA-9035E) for SF worksite', 'Counsel', 'Certified 2026-03-27', 'high', 'done'),
        task('File I-129 with premium processing', 'Counsel', 'Filed 2026-04-02 — receipt issued', 'high', 'done'),
        task('Assemble right-to-control evidence (board resolution, cap table)', 'Employer', 'Done', 'high', 'done'),
        task('Maintain Public Access File at SF worksite', 'Employer', 'Ongoing', 'high'),
        task('Update payroll to LCA wage on H-1B effective date', 'Employer', 'By 2026-10-01', 'medium'),
        task('Book consular stamping in Ho Chi Minh City (if travel planned)', 'Immigrant', 'Before next trip', 'medium'),
        task('Calendar H-1B max-out and green-card start (PERM by mid-2027)', 'Counsel', '2027-06-01', 'medium')
      ],
      findings: [], notes: 'Founder-adjacent H-1B: Bao holds 1.25% equity, so the petition includes a board resolution and cap table establishing deep24\'s right to control (hire/supervise/terminate). FY2027 cap registration selected; I-129 filed April 2 with premium processing at the California Service Center. STEM OPT EAD runs to Jan 2027, so no cap-gap risk for the Oct 1 start. PERM kickoff recommended mid-2027.', messages: []
    },

    // ---------------- USA · WORK · China -> USA · H-1B ----------------
    {
      id: 'seed_us_work', createdAt: ts(40), updatedAt: ts(2),
      applicantName: 'Wei Chen', originCountry: 'China', destinationCountry: 'USA',
      pathway: 'work', visaType: 'H-1B', employer: 'Northwind Robotics, Inc.', ownerRole: 'employer',
      facts: {
        stage: 'Filing', 'Passport No.': 'E12345678', 'Date of Birth': '1993-04-18',
        Nationality: 'China', 'Passport Expiry': '2031-08-22', Position: 'Senior Robotics Engineer',
        'Annual Salary': 'USD 152,000', 'Status Valid Until': '2027-09-30', 'Worksite': 'Austin, TX',
        'Current Status': 'F-1 STEM OPT', 'SEVIS / EAD': 'EAD valid to 2026-09-28', 'Degree': 'M.S. Robotics, Georgia Tech',
        'Receipt Number': 'EAC2590012345', 'Premium Processing': 'Yes (15-day)', 'Dependents': 'Spouse (H-4) — Lin Zhao'
      },
      documents: [
        doc('Passport — China', `PEOPLE'S REPUBLIC OF CHINA — PASSPORT
Type: P   Country Code: CHN
Passport No.: E12345678
Surname: CHEN
Given Names: WEI
Nationality: CHINESE
Date of Birth: 1993-04-18
Sex: M
Place of Birth: SHANGHAI
Date of Issue: 2021-08-23
Date of Expiry: 2031-08-22
Authority: MINISTRY OF PUBLIC SECURITY
P<CHNCHEN<<WEI<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
E123456789CHN9304182M3108224<<<<<<<<<<<<<<06`),
        doc('I-797C Receipt Notice', `U.S. CITIZENSHIP AND IMMIGRATION SERVICES
Form I-797C, Notice of Action
Receipt Number: EAC2590012345
Notice Type: Receipt Notice
Case Type: I-129, Petition for a Nonimmigrant Worker
Classification: H-1B
Petitioner: Northwind Robotics, Inc.
Beneficiary: Chen, Wei
Received Date: 2026-03-25
Notice Date: 2026-03-28
Valid From: 2026-10-01
Valid To: 2029-09-30
Service Center: Texas Service Center
Premium Processing: Requested (Form I-907)`),
        doc('Labor Condition Application (LCA)', `LABOR CONDITION APPLICATION (ETA-9035)
Case Number: I-203-26075-123456
Case Status: CERTIFIED
Job Title: Senior Robotics Engineer
SOC Code: 17-2199 (Engineers, All Other)
Wage Level: Level III
Prevailing Wage: USD 141,300 per year
Offered Wage: USD 152,000 per year
Worksite: 4400 Industrial Blvd, Austin, TX 78744
Period of Employment: 2026-10-01 to 2029-09-30
Employer: Northwind Robotics, Inc.
Public Disclosure: Posted 2026-03-12 to 2026-03-22`),
        doc('Employment Offer Letter', `NORTHWIND ROBOTICS, INC.
Date: 2026-02-10
Re: Offer of Employment
Employee: Wei Chen
Position: Senior Robotics Engineer
Annual Salary: USD 152,000
Start Date: 2026-10-01 (subject to H-1B approval)
Full-time, exempt. Reports to VP of Engineering.
Duties: Design and validate autonomous motion-planning systems; lead a team of four; own the perception-to-control pipeline. Minimum requirement: Master's in Robotics, Mechanical, or Electrical Engineering.`),
        doc('Resume / CV', `WEI CHEN — ROBOTICS ENGINEER
EDUCATION
- M.S. Robotics, Georgia Institute of Technology, 2021 (GPA 3.9/4.0)
- B.Eng. Automation, Shanghai Jiao Tong University, 2015
EXPERIENCE
- Robotics Engineer, Northwind Robotics (F-1 OPT/STEM OPT), 2021-present
- Research Assistant, GT Locomotion Lab, 2019-2021
PUBLICATIONS: 4 peer-reviewed (ICRA, IROS). PATENTS: 1 pending (motion planning).`),
        doc('Degree + Credential Evaluation', `EDUCATIONAL CREDENTIAL EVALUATION
Candidate: Wei Chen
Foreign Credential: B.Eng. Automation, Shanghai Jiao Tong University (2015)
U.S. Equivalency: Bachelor of Science in Engineering
U.S. Graduate Degree: M.S. Robotics, Georgia Institute of Technology (2021) — verified
Evaluator: World Education Services (WES) — Reference 26-447821`),
        doc('Prior I-94 Arrival/Departure Record', `U.S. CBP — I-94 ADMISSION RECORD
Name: CHEN, WEI
Admission (I-94) Number: 567812340A1
Class of Admission: F-1 (D/S)
Most Recent Entry: 2021-08-30 (Atlanta, ATL)
EAD (STEM OPT) valid through: 2026-09-28
Note: Cap-gap extension applies through 2026-09-30 once H-1B petition is filed and pending/approved.`)
      ],
      tasks: [
        task('File I-129 H-1B petition with USCIS', 'Counsel', 'Approved — receipt issued', 'high', 'done'),
        task('Confirm cap-gap coverage between OPT EAD and H-1B start', 'Counsel', 'Done', 'high', 'done'),
        task('Confirm consular appointment in Guangzhou', 'Immigrant', 'Aug 2026', 'high'),
        task('Prepare worksite Public Access File (PAF)', 'Employer', 'Before start date', 'high'),
        task('Update payroll to LCA wage on H-1B start', 'Employer', 'By 2026-10-01', 'medium'),
        task('Prepare H-4 application for spouse (Lin Zhao)', 'Counsel', 'Sept 2026', 'medium')
      ],
      findings: [], notes: 'Cap-subject H-1B selected in the FY2027 lottery; premium processing. Beneficiary currently on STEM OPT — cap-gap bridges to the Oct 1 start. Petition approved; consular stamping in Guangzhou next. Spouse will file H-4.', messages: []
    },

    // ---------------- USA · STUDENT · India -> USA · F-1 ----------------
    {
      id: 'seed_us_student', createdAt: ts(25), updatedAt: ts(1),
      applicantName: 'Aarav Sharma', originCountry: 'India', destinationCountry: 'USA',
      pathway: 'student', visaType: 'F-1', employer: '', ownerRole: 'immigrant',
      facts: {
        stage: 'Onboarding', 'Passport No.': 'Z3456789', 'Date of Birth': '2004-11-02',
        Nationality: 'India', 'Passport Expiry': '2029-01-15', School: 'Carnegie Tech University',
        Program: 'M.S. Computer Science', 'SEVIS ID': 'N0031245678'
      },
      documents: [
        doc('Passport — India', `REPUBLIC OF INDIA — PASSPORT
Type: P   Country Code: IND
Passport No.: Z3456789
Surname: SHARMA
Given Name: AARAV
Nationality: INDIAN
Date of Birth: 2004-11-02
Sex: M
Place of Birth: PUNE
Date of Issue: 2019-01-16
Date of Expiry: 2029-01-15`),
        doc('Form I-20', `DEPARTMENT OF HOMELAND SECURITY
Form I-20, Certificate of Eligibility for Nonimmigrant Student Status
SEVIS ID: N0031245678
School: Carnegie Tech University
Program: Master of Science in Computer Science
Education Level: MASTER'S
Program Start Date: 2026-08-24
Program End Date: 2028-05-15
Estimated Cost (per year): USD 58,400
Funding: Personal/Family USD 60,000`),
        doc('SEVIS I-901 Fee Receipt', `I-901 SEVIS FEE PAYMENT CONFIRMATION
Name: Aarav Sharma
SEVIS ID: N0031245678
Amount Paid: USD 350
Status: PAID
Payment Date: 2026-06-01`),
        doc('Financial Support Affidavit', `AFFIDAVIT OF FINANCIAL SUPPORT
Sponsor: Rajesh Sharma (father)
Bank: State Bank of India
Available Balance: USD 71,250 equivalent
Annual Income: USD 96,000 equivalent
Relationship: Parent`)
      ],
      tasks: [
        task('Pay SEVIS I-901 fee', 'Immigrant', 'Done', 'high', 'done'),
        task('Complete DS-160 and book visa interview', 'Immigrant', 'July 2026', 'high'),
        task('Prepare financial documents for interview', 'Immigrant', 'July 2026', 'medium')
      ],
      findings: [], notes: 'New F-1 for Fall 2026 intake. I-20 issued; visa interview pending.', messages: []
    },

    // ---------------- USA · TRAVEL · Brazil -> USA · B-1/B-2 ----------------
    {
      id: 'seed_us_travel', createdAt: ts(12), updatedAt: ts(1),
      applicantName: 'Mariana Costa', originCountry: 'Brazil', destinationCountry: 'USA',
      pathway: 'travel', visaType: 'B-1 / B-2', employer: '', ownerRole: 'immigrant',
      facts: {
        stage: 'Filing', 'Passport No.': 'FK987654', 'Date of Birth': '1990-07-09',
        Nationality: 'Brazil', 'Passport Expiry': '2030-03-30', 'Trip Purpose': 'Tourism + business conference'
      },
      documents: [
        doc('Passport — Brazil', `REPÚBLICA FEDERATIVA DO BRASIL — PASSPORT
Type: P   Country Code: BRA
Passport No.: FK987654
Surname: COSTA
Given Names: MARIANA
Nationality: BRAZILIAN
Date of Birth: 1990-07-09
Date of Expiry: 2030-03-30`),
        doc('DS-160 Confirmation', `U.S. DEPARTMENT OF STATE
Nonimmigrant Visa Application (DS-160) Confirmation
Confirmation No.: AA00XYZ123
Applicant: Mariana Costa
Visa Class: B1/B2
Purpose of Travel: Tourism and business meetings
Intended Date of Arrival: 2026-09-15`),
        doc('Travel Itinerary & Ties', `ITINERARY
Arrival: 2026-09-15 (San Francisco)
Departure: 2026-09-29
Employer in Brazil: Banco Atlântico (Marketing Director) — approved leave
Property owned in São Paulo; two dependent children remain in Brazil.`)
      ],
      tasks: [
        task('Submit DS-160', 'Immigrant', 'Done', 'high', 'done'),
        task('Attend visa interview (São Paulo consulate)', 'Immigrant', 'Aug 2026', 'high')
      ],
      findings: [], notes: 'Strong home-country ties documented to support nonimmigrant intent.', messages: []
    },

    // ---------------- CANADA · WORK · China -> Canada · LMIA Work Permit ----------------
    {
      id: 'seed_ca_work', createdAt: ts(33), updatedAt: ts(2),
      applicantName: 'Li Na', originCountry: 'China', destinationCountry: 'Canada',
      pathway: 'work', visaType: 'Work permit (LMIA)', employer: 'Maple Grid Energy Ltd.', ownerRole: 'employer',
      facts: {
        stage: 'Filing', 'Passport No.': 'EH2233445', 'Date of Birth': '1988-12-01',
        Nationality: 'China', 'Passport Expiry': '2028-05-10', Position: 'Power Systems Analyst',
        'NOC Code': '21300', 'LMIA Number': 'A-2026-0456789', 'Worksite': 'Calgary, AB'
      },
      documents: [
        doc('Passport — China', `PEOPLE'S REPUBLIC OF CHINA — PASSPORT
Passport No.: EH2233445
Surname: LI
Given Names: NA
Nationality: CHINESE
Date of Birth: 1988-12-01
Date of Expiry: 2028-05-10`),
        doc('Positive LMIA', `EMPLOYMENT AND SOCIAL DEVELOPMENT CANADA
Labour Market Impact Assessment (LMIA) — DECISION
LMIA Number: A-2026-0456789
Decision: POSITIVE
Employer: Maple Grid Energy Ltd.
Job Title: Power Systems Analyst
NOC: 21300
Wage: CAD 96,000 per year
Work Location: Calgary, Alberta
Number of Positions: 1`),
        doc('户口簿 / Household Registration (Chinese)', `中华人民共和国居民户口簿
户主姓名：李娜
出生日期：1988年12月01日
籍贯：北京市
婚姻状况：已婚
工作单位：北京电力科学研究院
登记日期：2010年09月15日`),
        doc('Offer of Employment', `MAPLE GRID ENERGY LTD.
Offer of Employment (supporting LMIA A-2026-0456789)
Candidate: Li Na
Position: Power Systems Analyst (NOC 21300)
Salary: CAD 96,000/year
Start: Upon work permit issuance
Location: Calgary, AB`)
      ],
      tasks: [
        task('Obtain positive LMIA', 'Employer', 'Done', 'high', 'done'),
        task('Submit work permit application (IMM 1295)', 'Counsel', 'In progress', 'high'),
        task('Complete biometrics in Beijing', 'Immigrant', 'This month', 'medium')
      ],
      findings: [], notes: 'LMIA-based work permit. Household registration requires certified translation.', messages: []
    },

    // ---------------- CANADA · STUDENT · Nigeria -> Canada · Study Permit ----------------
    {
      id: 'seed_ca_student', createdAt: ts(20), updatedAt: ts(1),
      applicantName: 'Chidi Okafor', originCountry: 'Nigeria', destinationCountry: 'Canada',
      pathway: 'student', visaType: 'Study permit', employer: '', ownerRole: 'immigrant',
      facts: {
        stage: 'Onboarding', 'Passport No.': 'A09876543', 'Date of Birth': '2003-05-21',
        Nationality: 'Nigeria', 'Passport Expiry': '2029-09-12', School: 'University of Toronto',
        Program: 'B.A.Sc. Engineering Science', 'DLI Number': 'O19332052392'
      },
      documents: [
        doc('Passport — Nigeria', `FEDERAL REPUBLIC OF NIGERIA — PASSPORT
Passport No.: A09876543
Surname: OKAFOR
Given Names: CHIDI
Nationality: NIGERIAN
Date of Birth: 2003-05-21
Date of Expiry: 2029-09-12`),
        doc('Letter of Acceptance (DLI)', `UNIVERSITY OF TORONTO
Office of Admissions — Letter of Acceptance
Student: Chidi Okafor
DLI Number: O19332052392
Program: Bachelor of Applied Science, Engineering Science
Start Date: 2026-09-08
Tuition (year 1): CAD 62,250
Status: Unconditional offer accepted`),
        doc('Provincial Attestation Letter (PAL)', `PROVINCE OF ONTARIO
Provincial Attestation Letter (PAL)
Issued To: Chidi Okafor
Institution: University of Toronto
PAL Reference: ON-PAL-2026-118245
Confirms allocation under the provincial cap.`),
        doc('Proof of Funds', `GUARANTEED INVESTMENT CERTIFICATE (GIC)
Holder: Chidi Okafor
Institution: Scotiabank
Amount: CAD 20,635
Plus tuition paid: CAD 62,250
Sponsor income (father): CAD 88,000 equivalent/year`)
      ],
      tasks: [
        task('Receive Letter of Acceptance + PAL', 'Immigrant', 'Done', 'high', 'done'),
        task('Submit study permit application', 'Immigrant', 'This week', 'high'),
        task('Book biometrics in Lagos', 'Immigrant', 'Next week', 'medium')
      ],
      findings: [], notes: 'Fall 2026 intake. PAL secured under Ontario cap; proof of funds meets GIC requirement.', messages: []
    },

    // ---------------- CANADA · TRAVEL · Philippines -> Canada · Visitor Visa (TRV) ----------------
    {
      id: 'seed_ca_travel', createdAt: ts(15), updatedAt: ts(1),
      applicantName: 'Maria Santos', originCountry: 'Philippines', destinationCountry: 'Canada',
      pathway: 'travel', visaType: 'Visitor visa (TRV)', employer: '', ownerRole: 'immigrant',
      facts: {
        stage: 'Compliance', 'Passport No.': 'P1234567A', 'Date of Birth': '1985-02-14',
        Nationality: 'Philippines', 'Passport Expiry': '2028-11-20', 'Trip Purpose': 'Family visit'
      },
      documents: [
        doc('Passport — Philippines', `REPUBLIC OF THE PHILIPPINES — PASSPORT
Passport No.: P1234567A
Surname: SANTOS
Given Names: MARIA
Nationality: FILIPINO
Date of Birth: 1985-02-14
Date of Expiry: 2028-11-20`),
        doc('Invitation Letter', `LETTER OF INVITATION
Host: Ana Santos (Canadian citizen, sister)
Address: 88 Lakeshore Rd, Mississauga, ON
Guest: Maria Santos
Purpose: Family visit and grandchild's baptism
Duration: 2026-12-10 to 2027-01-08
Host will provide accommodation.`),
        doc('Proof of Funds & Ties', `SUPPORTING EVIDENCE
Employer: Manila General Hospital (Registered Nurse, 9 years) — approved leave
Bank balance: PHP 480,000
Property: condominium in Quezon City
Two children enrolled in school in Manila.`)
      ],
      tasks: [
        task('Submit TRV application (IMM 5257)', 'Immigrant', 'Done', 'high', 'done'),
        task('Complete biometrics', 'Immigrant', 'Done', 'medium', 'done'),
        task('Monitor application status', 'Ellis', 'Ongoing', 'low')
      ],
      findings: [], notes: 'Strong ties (employment, property, dependents). Awaiting decision.', messages: []
    },

    // ---------------- CANADA · STUDENT · China -> Canada · Study Permit ----------------
    {
      id: 'seed_ca_student_cn', createdAt: ts(22), updatedAt: ts(1),
      applicantName: 'Zhang Wei', originCountry: 'China', destinationCountry: 'Canada',
      pathway: 'student', visaType: 'Study permit', employer: '', ownerRole: 'immigrant',
      facts: {
        stage: 'Onboarding', 'Passport No.': 'EG5566778', 'Date of Birth': '2005-03-30',
        Nationality: 'China', 'Passport Expiry': '2030-07-19', School: 'University of British Columbia',
        Program: 'B.Sc. Computer Science', 'DLI Number': 'O19330231062'
      },
      documents: [
        doc('Passport — China', `PEOPLE'S REPUBLIC OF CHINA — PASSPORT
Passport No.: EG5566778
Surname: ZHANG
Given Names: WEI
Nationality: CHINESE
Date of Birth: 2005-03-30
Place of Birth: GUANGZHOU
Date of Expiry: 2030-07-19`),
        doc('Letter of Acceptance (DLI)', `UNIVERSITY OF BRITISH COLUMBIA
Office of the Registrar — Letter of Acceptance
Student: Zhang Wei
DLI Number: O19330231062
Program: Bachelor of Science, Computer Science
Intake: September 2026
Program Start Date: 2026-09-07
Tuition (year 1): CAD 45,600
Status: Offer accepted; deposit paid`),
        doc('Provincial Attestation Letter (PAL)', `PROVINCE OF BRITISH COLUMBIA
Provincial Attestation Letter (PAL)
Issued To: Zhang Wei
Institution: University of British Columbia
PAL Reference: BC-PAL-2026-204871
Confirms allocation under the provincial study-permit cap.`),
        doc('Proof of Funds (GIC + tuition)', `SCOTIABANK STUDENT GIC PROGRAM
Holder: Zhang Wei
GIC Amount: CAD 20,635
Tuition Paid to UBC: CAD 45,600
Sponsor: Zhang Ming (father) — annual income CAD 110,000 equivalent
Bank statements: 6 months provided`),
        doc('在职证明 / Employment Certificate of Sponsor (Chinese)', `在职证明
兹证明 张明 先生自 2008 年 6 月起在 广州市恒达科技有限公司 工作，
现任 技术总监 一职，年收入约人民币 760,000 元。
本公司同意其为子女 张伟 赴加拿大留学提供全部资金支持。
特此证明。
公司盖章   日期：2026年05月18日`),
        doc('IELTS Test Report', `IELTS ACADEMIC — TEST REPORT FORM
Candidate: Zhang Wei
Listening: 7.5  Reading: 8.0  Writing: 6.5  Speaking: 7.0
Overall Band Score: 7.5
Test Date: 2026-03-14`)
      ],
      tasks: [
        task('Accept offer and pay tuition deposit', 'Immigrant', 'Done', 'high', 'done'),
        task('Obtain PAL from British Columbia', 'Immigrant', 'Done', 'high', 'done'),
        task('Submit study permit application (IMM 1294)', 'Immigrant', 'This week', 'high'),
        task('Certified translation of Chinese employment certificate', 'Ellis', 'This week', 'medium'),
        task('Complete biometrics at VAC Guangzhou', 'Immigrant', 'Next week', 'medium')
      ],
      findings: [], notes: 'UBC Fall 2026. PAL secured under BC cap; sponsor employment certificate needs certified translation.', messages: []
    },

    // ---------------- CANADA · TRAVEL · China -> Canada · Visitor Visa (TRV) ----------------
    {
      id: 'seed_ca_travel_cn', createdAt: ts(10), updatedAt: ts(1),
      applicantName: 'Chen Yu', originCountry: 'China', destinationCountry: 'Canada',
      pathway: 'travel', visaType: 'Visitor visa (TRV)', employer: '', ownerRole: 'immigrant',
      facts: {
        stage: 'Filing', 'Passport No.': 'EJ4455667', 'Date of Birth': '1979-10-08',
        Nationality: 'China', 'Passport Expiry': '2029-06-25', 'Trip Purpose': 'Tourism (Banff & Vancouver)'
      },
      documents: [
        doc('Passport — China', `PEOPLE'S REPUBLIC OF CHINA — PASSPORT
Passport No.: EJ4455667
Surname: CHEN
Given Names: YU
Nationality: CHINESE
Date of Birth: 1979-10-08
Date of Expiry: 2029-06-25`),
        doc('Travel Itinerary', `ITINERARY — CANADA TOURISM
Arrival: 2026-10-05 (Vancouver, YVR)
Vancouver 3 nights → Banff/Lake Louise 4 nights → Calgary departure
Departure: 2026-10-16
Hotels prepaid; round-trip flights booked (CA-CN return confirmed).`),
        doc('在职证明 / Employment Certificate (Chinese)', `在职证明
兹证明 陈宇 女士在 上海明华贸易有限公司 担任 财务经理，
月薪约人民币 38,000 元，已批准其 2026 年 10 月 5 日至 16 日休假赴加拿大旅游。
其工作岗位予以保留。
公司盖章   日期：2026年08月20日`),
        doc('Proof of Funds & Ties', `SUPPORTING EVIDENCE
Bank balance: CNY 540,000 (China Merchants Bank, 6-month statement)
Property: apartment in Shanghai (title deed attached)
Family: spouse and one child remain in Shanghai
Previous travel: Schengen visa 2023, Japan visa 2024 (both complied)`)
      ],
      tasks: [
        task('Book refundable itinerary and accommodation', 'Immigrant', 'Done', 'medium', 'done'),
        task('Submit TRV application (IMM 5257)', 'Immigrant', 'This week', 'high'),
        task('Certified translation of employment certificate', 'Ellis', 'This week', 'medium'),
        task('Complete biometrics at VAC Shanghai', 'Immigrant', 'Next week', 'medium')
      ],
      findings: [], notes: 'Strong ties and clean prior travel history (Schengen, Japan). Tourism visit, fully funded.', messages: []
    },

    // ---------------- USA · STUDENT · China -> USA · F-1 ----------------
    {
      id: 'seed_us_student_cn', createdAt: ts(28), updatedAt: ts(1),
      applicantName: 'Liu Yang', originCountry: 'China', destinationCountry: 'USA',
      pathway: 'student', visaType: 'F-1', employer: '', ownerRole: 'immigrant',
      facts: {
        stage: 'Onboarding', 'Passport No.': 'EK7788990', 'Date of Birth': '2004-06-12',
        Nationality: 'China', 'Passport Expiry': '2030-02-28', School: 'Columbia University',
        Program: 'M.S. Financial Engineering', 'SEVIS ID': 'N0046612310', 'Estimated Cost / yr': 'USD 74,200'
      },
      documents: [
        doc('Passport — China', `PEOPLE'S REPUBLIC OF CHINA — PASSPORT
Passport No.: EK7788990
Surname: LIU
Given Names: YANG
Nationality: CHINESE
Date of Birth: 2004-06-12
Place of Birth: BEIJING
Date of Expiry: 2030-02-28`),
        doc('Form I-20', `DEPARTMENT OF HOMELAND SECURITY
Form I-20, Certificate of Eligibility for Nonimmigrant Student Status
SEVIS ID: N0046612310
School: Columbia University (SEVP # NYC214F00123000)
Program: Master of Science in Financial Engineering
Education Level: MASTER'S
Program Start Date: 2026-08-31
Program End Date: 2027-12-20
Estimated Cost (per year): USD 74,200
Funding: Personal/Family USD 80,000`),
        doc('SEVIS I-901 Fee Receipt', `I-901 SEVIS FEE PAYMENT CONFIRMATION
Name: Liu Yang
SEVIS ID: N0046612310
Amount Paid: USD 350
Status: PAID
Payment Date: 2026-06-08`),
        doc('Bank Statement & Funding', `BANK OF CHINA — DEPOSIT CERTIFICATE
Account Holder: Liu Jianguo (father)
Certified Balance: USD 162,000 equivalent (CNY 1,170,000)
Term Deposit held > 6 months
Annual household income: USD 140,000 equivalent`),
        doc('在职证明 / Sponsor Employment Certificate (Chinese)', `在职证明
兹证明 刘建国 先生在 中国银行北京分行 担任 高级经理，
年收入约人民币 980,000 元，同意资助其女 刘洋 赴美国攻读硕士学位。
特此证明。
公司盖章   日期：2026年05月30日`),
        doc('TOEFL Score Report', `TOEFL iBT — SCORE REPORT
Examinee: Liu Yang
Reading 29  Listening 28  Speaking 25  Writing 27
Total: 109 / 120
Test Date: 2026-02-21`)
      ],
      tasks: [
        task('Pay SEVIS I-901 fee', 'Immigrant', 'Done', 'high', 'done'),
        task('Complete DS-160 and book interview (Beijing)', 'Immigrant', 'July 2026', 'high'),
        task('Certified translation of sponsor employment certificate', 'Ellis', 'This week', 'medium'),
        task('Prepare ties + funding evidence for interview', 'Immigrant', 'July 2026', 'medium')
      ],
      findings: [], notes: 'Columbia MSFE, Fall 2026. I-20 issued; funding strong. Sponsor certificate needs certified translation; interview prep underway.', messages: []
    },

    // ---------------- USA · WORK · India -> USA · H-1B ----------------
    {
      id: 'seed_us_work_in', createdAt: ts(36), updatedAt: ts(2),
      applicantName: 'Priya Patel', originCountry: 'India', destinationCountry: 'USA',
      pathway: 'work', visaType: 'H-1B', employer: 'Cobalt Health Systems, Inc.', ownerRole: 'employer',
      facts: {
        stage: 'Compliance', 'Passport No.': 'P8123456', 'Date of Birth': '1995-09-27',
        Nationality: 'India', 'Passport Expiry': '2032-04-11', Position: 'Data Scientist',
        'Annual Salary': 'USD 138,500', 'Worksite': 'Boston, MA', 'Current Status': 'H-1B (transfer)',
        'NOC / SOC': '15-2051 Data Scientists', 'Receipt Number': 'WAC2690045678', 'Priority Date (I-140)': '2026-05-02 (EB-2)'
      },
      documents: [
        doc('Passport — India', `REPUBLIC OF INDIA — PASSPORT
Passport No.: P8123456
Surname: PATEL
Given Name: PRIYA
Nationality: INDIAN
Date of Birth: 1995-09-27
Place of Birth: AHMEDABAD
Date of Expiry: 2032-04-11`),
        doc('I-797B Approval (H-1B transfer)', `U.S. CITIZENSHIP AND IMMIGRATION SERVICES
Form I-797B, Notice of Action
Receipt Number: WAC2690045678
Notice Type: Approval Notice
Case Type: I-129, Petition for a Nonimmigrant Worker
Classification: H-1B (Change of Employer)
Petitioner: Cobalt Health Systems, Inc.
Beneficiary: Patel, Priya
Valid From: 2026-06-15  Valid To: 2029-06-14
Note: Consular/CBP I-94 to be updated; AC21 portability used at filing.`),
        doc('Labor Condition Application (LCA)', `LABOR CONDITION APPLICATION (ETA-9035)
Case Status: CERTIFIED
Job Title: Data Scientist
SOC Code: 15-2051
Wage Level: Level II
Prevailing Wage: USD 121,800 per year
Offered Wage: USD 138,500 per year
Worksite: 200 Seaport Blvd, Boston, MA 02210`),
        doc('PERM / I-140 Approval (EB-2)', `USCIS — I-140 IMMIGRANT PETITION
Classification: EB-2 (Advanced Degree)
Petitioner: Cobalt Health Systems, Inc.
Beneficiary: Priya Patel
PERM Certified: 2026-04-18 (ETA-9089 Case A-26009-55512)
I-140 Status: APPROVED
Priority Date: 2026-05-02
Note: Visa bulletin retrogression applies for India EB-2 — adjustment of status not yet current.`),
        doc('Degree + WES Evaluation', `EDUCATIONAL CREDENTIAL EVALUATION (WES)
Beneficiary: Priya Patel
Foreign Credential: M.Tech Computer Science, IIT Bombay (2018)
U.S. Equivalency: Master's degree
Bachelor: B.E. Computer Engineering, University of Mumbai (2016)
Reference: 26-559034`),
        doc('Pay Stubs (status maintenance)', `COBALT HEALTH SYSTEMS — PAYROLL SUMMARY
Employee: Priya Patel
Gross (last 3 months): USD 11,541/mo (annualized USD 138,500)
Status: Active, full-time
Confirms continuous H-1B wage compliance post-transfer.`)
      ],
      tasks: [
        task('File H-1B change-of-employer (AC21 portability)', 'Counsel', 'Approved', 'high', 'done'),
        task('Update I-9 and payroll to LCA wage', 'Employer', 'Done', 'high', 'done'),
        task('Maintain Public Access File for new worksite', 'Employer', 'Ongoing', 'medium'),
        task('Monitor India EB-2 priority date for I-485', 'Counsel', 'Ongoing', 'medium'),
        task('Track H-1B expiry and AC21 3-year extensions', 'Ellis', 'Ongoing', 'low')
      ],
      findings: [], notes: 'H-1B transfer approved via AC21 portability; EB-2 I-140 approved with 2026 priority date (India retrogression — green card wait). Compliance and PAF are the active items.', messages: []
    },

    // ---------------- CANADA · STUDENT · India -> Canada · Study Permit (SDS) ----------------
    {
      id: 'seed_ca_student_in', createdAt: ts(18), updatedAt: ts(1),
      applicantName: 'Rohan Gupta', originCountry: 'India', destinationCountry: 'Canada',
      pathway: 'student', visaType: 'Study permit (SDS)', employer: '', ownerRole: 'immigrant',
      facts: {
        stage: 'Filing', 'Passport No.': 'S6677889', 'Date of Birth': '2003-01-19',
        Nationality: 'India', 'Passport Expiry': '2031-10-05', School: 'University of Waterloo',
        Program: 'B.Math Computer Science', 'DLI Number': 'O19395848172', 'Stream': 'Student Direct Stream (SDS)'
      },
      documents: [
        doc('Passport — India', `REPUBLIC OF INDIA — PASSPORT
Passport No.: S6677889
Surname: GUPTA
Given Name: ROHAN
Nationality: INDIAN
Date of Birth: 2003-01-19
Place of Birth: NEW DELHI
Date of Expiry: 2031-10-05`),
        doc('Letter of Acceptance (DLI)', `UNIVERSITY OF WATERLOO
Office of the Registrar — Letter of Acceptance
Student: Rohan Gupta
DLI Number: O19395848172
Program: Bachelor of Mathematics, Computer Science (Co-op)
Program Start Date: 2026-09-08
Tuition (year 1): CAD 49,300
Status: Offer accepted; deposit CAD 5,000 paid`),
        doc('Provincial Attestation Letter (PAL)', `PROVINCE OF ONTARIO
Provincial Attestation Letter (PAL)
Issued To: Rohan Gupta
Institution: University of Waterloo
PAL Reference: ON-PAL-2026-339014
Confirms allocation under the provincial study-permit cap.`),
        doc('GIC + Tuition (SDS requirement)', `SCOTIABANK STUDENT GIC PROGRAM
Holder: Rohan Gupta
GIC Amount: CAD 20,635
First-year tuition paid to UWaterloo: CAD 49,300
Confirms SDS financial requirements satisfied.`),
        doc('IELTS Academic Test Report', `IELTS ACADEMIC — TEST REPORT FORM
Candidate: Rohan Gupta
Listening 8.0  Reading 7.5  Writing 6.5  Speaking 7.5
Overall Band: 7.5 (each band >= 6.0 — meets SDS)
Test Date: 2026-04-05`),
        doc('Statement of Purpose', `STATEMENT OF PURPOSE
I am applying for a study permit to pursue the BMath Computer Science (Co-op) program at the University of Waterloo. My goal is to specialize in machine learning and return to India to join my family's analytics firm. The co-op program and Waterloo's reputation make it the ideal fit. I have funds via a GIC and prepaid tuition, and strong ties to India (family business, property).`)
      ],
      tasks: [
        task('Accept offer and pay tuition + GIC', 'Immigrant', 'Done', 'high', 'done'),
        task('Obtain PAL (Ontario)', 'Immigrant', 'Done', 'high', 'done'),
        task('Submit SDS study permit application (IMM 1294)', 'Immigrant', 'This week', 'high'),
        task('Upfront medical exam (panel physician, Delhi)', 'Immigrant', 'This week', 'medium'),
        task('Complete biometrics at VAC New Delhi', 'Immigrant', 'Next week', 'medium')
      ],
      findings: [], notes: 'Waterloo CS Co-op, Fall 2026. SDS-eligible: IELTS 7.5, GIC + prepaid tuition, PAL secured. Medical and biometrics outstanding.', messages: []
    },

    // ---------------- USA · TRAVEL · China -> USA · B-1/B-2 ----------------
    {
      id: 'seed_us_travel_cn', createdAt: ts(11), updatedAt: ts(1),
      applicantName: 'Wang Fang', originCountry: 'China', destinationCountry: 'USA',
      pathway: 'travel', visaType: 'B-1 / B-2', employer: '', ownerRole: 'immigrant',
      facts: {
        stage: 'Filing', 'Passport No.': 'EL3344556', 'Date of Birth': '1976-03-22',
        Nationality: 'China', 'Passport Expiry': '2029-12-01', 'Trip Purpose': 'Tourism + visit daughter (US PhD student)'
      },
      documents: [
        doc('Passport — China', `PEOPLE'S REPUBLIC OF CHINA — PASSPORT
Passport No.: EL3344556
Surname: WANG
Given Names: FANG
Nationality: CHINESE
Date of Birth: 1976-03-22
Place of Birth: HANGZHOU
Date of Expiry: 2029-12-01`),
        doc('DS-160 Confirmation', `U.S. DEPARTMENT OF STATE
Nonimmigrant Visa Application (DS-160) Confirmation
Confirmation No.: AA00CN8842
Applicant: Wang Fang
Visa Class: B1/B2
Purpose of Travel: Tourism and visiting daughter
Intended Date of Arrival: 2026-11-20`),
        doc('Invitation Letter (daughter)', `LETTER OF INVITATION
Host: Wang Mei (daughter, F-1 PhD student, Stanford University)
SEVIS/I-20 on file; enrolled in Ph.D. Bioengineering
Guest: Wang Fang (mother)
Purpose: Family visit and Thanksgiving holiday
Duration: 2026-11-20 to 2026-12-18
Host provides accommodation in Palo Alto, CA.`),
        doc('在职证明 / Employment Certificate (Chinese)', `在职证明
兹证明 王芳 女士在 杭州华信会计师事务所 担任 合伙人，
年收入约人民币 620,000 元，已批准其 2026 年 11 月赴美探亲旅游休假，
其职位与岗位予以保留。
公司盖章   日期：2026年09月02日`),
        doc('Proof of Funds & Ties', `SUPPORTING EVIDENCE
Bank balance: CNY 920,000 (ICBC, 6-month statement)
Property: two apartments in Hangzhou (deeds attached)
Spouse remains in China (employed); prior US B1/B2 in 2018 (complied)
Approved leave letter from accounting firm.`)
      ],
      tasks: [
        task('Submit DS-160', 'Immigrant', 'Done', 'high', 'done'),
        task('Book visa interview (Shanghai consulate)', 'Immigrant', 'Oct 2026', 'high'),
        task('Certified translation of employment certificate', 'Ellis', 'This week', 'medium'),
        task('Prepare ties + funding evidence for interview', 'Immigrant', 'Oct 2026', 'medium')
      ],
      findings: [], notes: 'Visiting US-based daughter; strong ties (business partner role, property, spouse in China) and clean prior US travel.', messages: []
    },

    // ---------------- USA · TRAVEL · India -> USA · B-1/B-2 ----------------
    {
      id: 'seed_us_travel_in', createdAt: ts(9), updatedAt: ts(1),
      applicantName: 'Anjali Rao', originCountry: 'India', destinationCountry: 'USA',
      pathway: 'travel', visaType: 'B-1 / B-2', employer: '', ownerRole: 'immigrant',
      facts: {
        stage: 'Filing', 'Passport No.': 'R4455667', 'Date of Birth': '1968-08-14',
        Nationality: 'India', 'Passport Expiry': '2030-05-19', 'Trip Purpose': 'Visit son + grandchild (US H-1B family)'
      },
      documents: [
        doc('Passport — India', `REPUBLIC OF INDIA — PASSPORT
Passport No.: R4455667
Surname: RAO
Given Name: ANJALI
Nationality: INDIAN
Date of Birth: 1968-08-14
Place of Birth: HYDERABAD
Date of Expiry: 2030-05-19`),
        doc('DS-160 Confirmation', `U.S. DEPARTMENT OF STATE
Nonimmigrant Visa Application (DS-160) Confirmation
Confirmation No.: AA00IN2290
Applicant: Anjali Rao
Visa Class: B1/B2
Purpose of Travel: Visiting son and newborn grandchild
Intended Date of Arrival: 2026-10-10`),
        doc('Invitation Letter (son)', `LETTER OF INVITATION
Host: Karthik Rao (son, H-1B, Microsoft, Redmond WA)
Spouse on H-4; newborn US-citizen grandchild
Guest: Anjali Rao (mother)
Purpose: Help with newborn, family visit
Duration: 2026-10-10 to 2027-01-05
Host provides accommodation and covers expenses (Form I-134 attached).`),
        doc('Pension & Funds', `SUPPORTING EVIDENCE
Retired government school principal — monthly pension INR 78,000
Bank balance: INR 2,450,000 (State Bank of India, 6-month statement)
Owns family home in Hyderabad (title deed attached)
Spouse (retired) remains in India.`),
        doc('Affidavit of Support (I-134)', `FORM I-134 — DECLARATION OF FINANCIAL SUPPORT
Sponsor: Karthik Rao (H-1B, annual income USD 168,000)
Beneficiary: Anjali Rao (mother)
Sponsor agrees to maintain the beneficiary during her temporary US visit.`)
      ],
      tasks: [
        task('Submit DS-160', 'Immigrant', 'Done', 'high', 'done'),
        task('Book visa interview (Hyderabad consulate)', 'Immigrant', 'Sept 2026', 'high'),
        task('Prepare ties evidence (pension, property, spouse)', 'Immigrant', 'Sept 2026', 'medium')
      ],
      findings: [], notes: 'Parent visitor visa to help with newborn grandchild. Ties: pension, property, spouse in India. I-134 from H-1B son on file.', messages: []
    },

    // ---------------- CANADA · WORK · India -> Canada · Work Permit (LMIA) ----------------
    {
      id: 'seed_ca_work_in', createdAt: ts(30), updatedAt: ts(2),
      applicantName: 'Vikram Singh', originCountry: 'India', destinationCountry: 'Canada',
      pathway: 'work', visaType: 'Work permit (LMIA)', employer: 'Northern Spark Technologies Inc.', ownerRole: 'employer',
      facts: {
        stage: 'Filing', 'Passport No.': 'T7788991', 'Date of Birth': '1991-07-03',
        Nationality: 'India', 'Passport Expiry': '2031-03-15', Position: 'Senior Software Developer',
        'NOC Code': '21231', 'LMIA Number': 'A-2026-0781234', 'Worksite': 'Toronto, ON', 'Annual Salary': 'CAD 104,000'
      },
      documents: [
        doc('Passport — India', `REPUBLIC OF INDIA — PASSPORT
Passport No.: T7788991
Surname: SINGH
Given Name: VIKRAM
Nationality: INDIAN
Date of Birth: 1991-07-03
Place of Birth: CHANDIGARH
Date of Expiry: 2031-03-15`),
        doc('Positive LMIA', `EMPLOYMENT AND SOCIAL DEVELOPMENT CANADA
Labour Market Impact Assessment (LMIA) — DECISION
LMIA Number: A-2026-0781234
Decision: POSITIVE
Employer: Northern Spark Technologies Inc.
Job Title: Senior Software Developer
NOC: 21231 (Software developers and programmers)
Wage: CAD 104,000 per year
Work Location: Toronto, Ontario
Number of Positions: 1`),
        doc('Offer of Employment', `NORTHERN SPARK TECHNOLOGIES INC.
Offer of Employment (supporting LMIA A-2026-0781234)
Candidate: Vikram Singh
Position: Senior Software Developer (NOC 21231)
Salary: CAD 104,000/year + benefits
Start: Upon work permit issuance
Location: Toronto, ON (hybrid)`),
        doc('Education Credential Assessment (ECA)', `WORLD EDUCATION SERVICES (WES) — ECA
Applicant: Vikram Singh
Credential: B.Tech Computer Science, Punjab Engineering College (2013)
Canadian Equivalency: Bachelor's degree
Reference: 26-661250`),
        doc('Reference / Experience Letters', `EXPERIENCE SUMMARY
- Infosys Ltd. — Software Engineer (2013-2018), Bangalore
- Flipkart — Senior Developer (2018-2026), Bangalore
Total 12+ years; skills: Java, Go, distributed systems, cloud.
Reference letters from both employers on letterhead attached.`),
        doc('IELTS General Training', `IELTS GENERAL TRAINING — TEST REPORT FORM
Candidate: Vikram Singh
Listening 8.5  Reading 7.0  Writing 7.0  Speaking 7.5
Overall Band: 7.5
Test Date: 2026-03-28`)
      ],
      tasks: [
        task('Obtain positive LMIA', 'Employer', 'Done', 'high', 'done'),
        task('Submit work permit application (IMM 1295)', 'Counsel', 'This week', 'high'),
        task('Complete biometrics at VAC Chandigarh', 'Immigrant', 'Next week', 'medium'),
        task('Upfront medical exam (panel physician)', 'Immigrant', 'Next week', 'medium'),
        task('Assess Express Entry CRS for future PR', 'Ellis', 'Ongoing', 'low')
      ],
      findings: [], notes: 'LMIA-based work permit; strong profile (12 yrs experience, ECA, IELTS 7.5). Candidate may qualify for Express Entry PR later — Ellis to assess CRS.', messages: []
    },

    // ---------------- CANADA · TRAVEL · India -> Canada · Visitor Visa (TRV) ----------------
    {
      id: 'seed_ca_travel_in', createdAt: ts(8), updatedAt: ts(1),
      applicantName: 'Sunita Mehta', originCountry: 'India', destinationCountry: 'Canada',
      pathway: 'travel', visaType: 'Visitor visa (TRV)', employer: '', ownerRole: 'immigrant',
      facts: {
        stage: 'Filing', 'Passport No.': 'U9900112', 'Date of Birth': '1963-11-30',
        Nationality: 'India', 'Passport Expiry': '2028-08-09', 'Trip Purpose': 'Visit daughter (Canada PR) + grandchild'
      },
      documents: [
        doc('Passport — India', `REPUBLIC OF INDIA — PASSPORT
Passport No.: U9900112
Surname: MEHTA
Given Name: SUNITA
Nationality: INDIAN
Date of Birth: 1963-11-30
Place of Birth: JAIPUR
Date of Expiry: 2028-08-09`),
        doc('Invitation Letter (daughter)', `LETTER OF INVITATION
Host: Neha Mehta (daughter, Canadian permanent resident)
Address: 142 Brant St, Burlington, ON
Guest: Sunita Mehta (mother)
Purpose: Family visit; help after grandchild's birth
Duration: 2026-12-01 to 2027-03-01
Host (Software Manager) provides accommodation and support.`),
        doc('Host Financial Support', `HOST SUPPORTING DOCUMENTS
Neha Mehta — Notice of Assessment (CRA) income CAD 132,000
Employment letter (permanent, Software Manager)
Bank statements (3 months) and PR card copy attached.`),
        doc('Applicant Funds & Ties', `SUPPORTING EVIDENCE
Retired; widow's pension INR 42,000/month
Bank balance: INR 1,180,000 (HDFC Bank, 6-month statement)
Owns flat in Jaipur (title deed attached)
Son and extended family remain in India.
Prior travel: UK visitor visa 2019 (complied).`)
      ],
      tasks: [
        task('Complete IMM 5257 (TRV) application', 'Immigrant', 'Done', 'high', 'done'),
        task('Submit application + host documents', 'Immigrant', 'This week', 'high'),
        task('Complete biometrics at VAC Jaipur', 'Immigrant', 'Next week', 'medium')
      ],
      findings: [], notes: 'Parent super-visit (regular TRV) to daughter (Canadian PR). Strong host income + applicant ties (pension, property, prior compliant UK travel).', messages: []
    }
  ]
}
