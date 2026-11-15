      *----------------------------------------------------------------
      * RATETAB - MONTHLY RATE BY PRODUCT AND BAND
      *----------------------------------------------------------------
       01  RATE-TABLE-VALUES.
           05  FILLER              PIC X(18)
                                   VALUE 'PL0189024903290419'.
           05  FILLER              PIC X(18)
                                   VALUE 'PY0139015901790199'.
           05  FILLER              PIC X(18)
                                   VALUE 'MT0079008900990000'.
       01  RATE-TABLE REDEFINES RATE-TABLE-VALUES.
           05  RT-ENTRY            OCCURS 3 TIMES
                                   INDEXED BY RT-IX.
               10  RT-PROD-CD      PIC X(02).
               10  RT-RATE-PCT     PIC 99V99 OCCURS 4 TIMES.
